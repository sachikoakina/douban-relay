#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆瓣公开 RSS 中继（GitHub Actions 版）

设计约束对应：
1. 只从首次运行开始增量采集，不回补历史；
2. 用 (条目 id/link + 发布时间) 生成去重键，重复抓取不会重复追加；
3. 归档上限 max_store，超出后按时间淘汰最旧条目（允许滚动更替）；
4. 输出 rss.xml（item_limit 条）以及 presets 指定的多档条数文件；
5. 无新增条目时不改写文件，避免产生空提交。

依赖：feedparser
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import feedparser

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
FEED_DIR = os.path.join(ROOT, "feed")
DATA_PATH = os.path.join(DATA_DIR, "items.json")

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/elements/1.1/"
TZ = timezone(timedelta(hours=8))  # 北京时间，无夏令时
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CFG = {}


def _env(name):
    """读取环境变量，空字符串视为未设置。"""
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else ""


def derive_site_url():
    """用 Actions 注入的 GITHUB_REPOSITORY 推导 Pages 地址，免去手动填 site_url。"""
    repo = _env("GITHUB_REPOSITORY")  # 形如 owner/name
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{name}"


def load_cfg():
    """config.json 提供默认值，环境变量覆盖之（优先级：env > config.json > 内置默认）。

    这样公开仓库里可以只放占位值的 config.json，真实配置由
    GitHub Secrets / Variables 在运行时注入，不必写进仓库与 git 历史。
    """
    global CFG
    try:
        with open(CFG_PATH, encoding="utf-8") as fh:
            CFG = json.load(fh)
    except FileNotFoundError:
        CFG = {}

    # 单值项：环境变量名 -> (配置键, 类型)
    overrides = (
        ("DOUBAN_UID", "douban_uid", str),
        ("FEED_TITLE", "feed_title", str),
        ("FEED_DESCRIPTION", "feed_description", str),
        ("ITEM_LIMIT", "item_limit", int),
        ("MAX_STORE", "max_store", int),
    )
    for env_name, key, cast in overrides:
        value = _env(env_name)
        if value:
            try:
                CFG[key] = cast(value)
            except ValueError:
                sys.exit(f"环境变量 {env_name} 的值无法解析为{cast.__name__}：{value!r}")

    presets = _env("PRESETS")
    if presets:
        try:
            CFG["presets"] = [int(x) for x in presets.split(",") if x.strip()]
        except ValueError:
            sys.exit(f"环境变量 PRESETS 需为逗号分隔的整数，例如 15,30,50：{presets!r}")

    # 站点地址：显式 SITE_URL 优先，其次 config.json，最后按仓库名自动推导
    cfg_site = str(CFG.get("site_url") or "").strip()
    if "你的" in cfg_site:  # 老版本模板里的占位值，视为未配置
        cfg_site = ""
    CFG["site_url"] = (_env("SITE_URL") or cfg_site or derive_site_url()).rstrip("/")

    uid = str(CFG.get("douban_uid", "")).strip()
    if not uid.isdigit():
        sys.exit(
            "douban_uid 未配置或非法：\n"
            "  仓库部署：Settings → Secrets and variables → Actions，\n"
            "           新建 Variables（推荐）或 Secrets，名称 DOUBAN_UID，值为豆瓣数字 ID；\n"
            "  本地运行：修改 config.json 的 douban_uid，或设置环境变量 DOUBAN_UID。"
        )
    CFG["douban_uid"] = uid


def load_state():
    if not os.path.exists(DATA_PATH):
        return {"items": [], "render_sig": ""}
    with open(DATA_PATH, encoding="utf-8") as fh:
        state = json.load(fh)
    state.setdefault("items", [])
    state.setdefault("render_sig", "")
    return state


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def fetch(url):
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"抓取失败：{url}（{last_err}）")


def entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


CAT_MAP = (
    ("movie.douban.com", "movie"),
    ("book.douban.com", "book"),
    ("music.douban.com", "music"),
)
# 豆瓣把「收藏状态」写进标题前缀，原文保留，另存一份结构化字段
STATUS = ("想看", "在看", "看过", "想读", "在读", "读过", "想听", "在听", "听过")


def entry_cat(entry, fallback=""):
    """按条目链接的域名判定书 / 影 / 音，比 ?cat= 参数可靠。"""
    link = entry.get("link") or ""
    for dom, name in CAT_MAP:
        if dom in link:
            return name
    return fallback


def entry_status(title):
    for word in STATUS:
        if title.startswith(word):
            return word
    return ""


def merge_entries(state, entries, cat=""):
    """把一次抓取到的条目并入归档，返回新增数量。"""
    known = {item["key"] for item in state["items"]}
    added = 0
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for entry in entries:
        dt = entry_time(entry)
        iso = dt.isoformat().replace("+00:00", "Z") if dt else now_iso
        raw = (entry.get("id") or entry.get("link") or entry.get("title") or "") + "|" + iso
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
        if key in known:
            continue
        title = (entry.get("title") or "").strip()
        state["items"].append(
            {
                "key": key,
                # 原始 interests guid，供下游与豆瓣原 feed 做 join
                "guid": (entry.get("id") or "").strip(),
                "cat": entry_cat(entry, cat),
                "status": entry_status(title),
                "title": title,
                "link": (entry.get("link") or "").strip(),
                "published": iso,
                "updated": (
                    datetime(*entry["updated_parsed"][:6], tzinfo=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                    if entry.get("updated_parsed")
                    else iso
                ),
                "summary": entry.get("summary") or "",
                "author": entry.get("author") or "",
            }
        )
        known.add(key)
        added += 1

    state["items"].sort(key=lambda item: item.get("published", ""), reverse=True)
    keep = int(CFG.get("max_store", 300))
    state["items"] = state["items"][:keep]
    return added


def render_rss(state, limit, self_url):
    ET.register_namespace("atom", ATOM)
    ET.register_namespace("dc", DC)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = CFG.get("feed_title", "豆瓣增量中继")
    ET.SubElement(channel, "link").text = f"https://www.douban.com/people/{CFG['douban_uid']}/"
    ET.SubElement(channel, "description").text = CFG.get("feed_description", "")
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "ttl").text = "20"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(TZ))
    ET.SubElement(
        channel,
        f"{{{ATOM}}}link",
        {"href": self_url, "rel": "self", "type": "application/rss+xml"},
    )

    for item in state["items"][:limit]:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = item.get("title") or "（无标题）"
        ET.SubElement(node, "link").text = item.get("link") or ""
        # 优先回填豆瓣原始 interests id，保证与上游 feed 的 guid 一致
        ET.SubElement(node, "guid", {"isPermaLink": "false"}).text = (
            item.get("guid") or item["key"]
        )
        try:
            dt = datetime.fromisoformat(item["published"].replace("Z", "+00:00")).astimezone(TZ)
            ET.SubElement(node, "pubDate").text = format_datetime(dt)
        except (ValueError, KeyError):
            pass
        if item.get("cat"):
            ET.SubElement(node, "category").text = item["cat"]
        if item.get("author"):
            ET.SubElement(node, f"{{{DC}}}creator").text = item["author"]
        if item.get("status"):
            ET.SubElement(node, "category", {"domain": "douban:status"}).text = item["status"]
        ET.SubElement(node, "description").text = item.get("summary") or ""

    try:
        ET.indent(rss, space="  ")
    except AttributeError:  # Python < 3.9
        pass
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def write_feeds(state):
    os.makedirs(FEED_DIR, exist_ok=True)
    site = str(CFG.get("site_url", "")).rstrip("/")
    outputs = {"rss.xml": int(CFG.get("item_limit", 30))}
    for preset in CFG.get("presets", []):
        outputs[f"rss-{int(preset)}.xml"] = int(preset)

    for name, limit in outputs.items():
        self_url = f"{site}/{name}" if site else name
        payload = render_rss(state, limit, self_url)
        with open(os.path.join(FEED_DIR, name), "wb") as fh:
            fh.write(payload)

    # 清理已不存在的档位文件：改 PRESETS 或 item_limit 后不残留旧文件被一起发布
    for stale in sorted(os.listdir(FEED_DIR)):
        if stale.endswith(".xml") and stale not in outputs:
            os.remove(os.path.join(FEED_DIR, stale))
            print(f"已清理旧档位：{stale}")
    return outputs


def main():
    load_cfg()
    state = load_state()

    # 2026-10 实测：interests 接口已忽略 ?cat=movie|book|music，四种写法返回字节一致，
    # 因此只抓一次，书/影/音由条目链接域名判定（entry_cat），避免 3 倍请求与错标。
    url = f"https://www.douban.com/feed/people/{CFG['douban_uid']}/interests"
    try:
        raw = fetch(url)
    except Exception as exc:
        sys.exit(f"抓取失败，本次不写入任何文件：{exc}")

    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        sys.exit(f"解析失败，本次不写入任何文件：{parsed.bozo_exception}")
    if not parsed.entries:
        sys.exit("抓到 0 条，本次不写入任何文件")

    cats = {}
    for entry in parsed.entries:
        name = entry_cat(entry) or "unknown"
        cats[name] = cats.get(name, 0) + 1
    added_total = merge_entries(state, parsed.entries)
    summary = "、".join(f"{k} {v}" for k, v in cats.items())
    print(f"抓到 {len(parsed.entries)} 条（{summary}），新增 {added_total} 条")

    sig_src = json.dumps(
        {
            "limit": CFG.get("item_limit"),
            "presets": CFG.get("presets"),
            "title": CFG.get("feed_title"),
            "desc": CFG.get("feed_description"),
            "site": CFG.get("site_url"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    sig = hashlib.sha1(sig_src.encode("utf-8")).hexdigest()[:12]
    need_render = added_total > 0 or state.get("render_sig") != sig or not os.path.exists(
        os.path.join(FEED_DIR, "rss.xml")
    )

    if need_render:
        outputs = write_feeds(state)
        state["render_sig"] = sig
        print("已生成：" + "、".join(outputs.keys()))
    else:
        print("无新增条目，跳过写入")

    save_state(state)
    total = len(state["items"])
    print(f"归档总数 {total} 条（上限 {CFG.get('max_store')}）；本次新增 {added_total} 条")


if __name__ == "__main__":
    main()
