# 豆瓣公开 RSS 增量中继

把豆瓣「只有最近 10 条」的公开 RSS，变成一条**只增不减、去重、条数可自定义**的 RSS 链接。

不需要服务器、不需要域名、不需要运维：抓取与发布全部由 GitHub 托管执行，归档数据存在你自己的仓库里。

## 它如何满足你的约束

| 你的要求 | 实现方式 |
|---|---|
| 免费、无需自部署 | GitHub Actions（公开仓库不计分钟数）+ GitHub Pages，无服务器 |
| 定时拉取豆瓣公开 RSS | 每 20 分钟抓取 `/feed/people/{uid}/interests`（`?cat=` 已失效，见下文） |
| 增量追加到自有存储 | 追加写入 `data/items.json`，提交到你的仓库，可随时导出 |
| 去重 | 以「条目 id/link + 发布时间」生成去重键，重复抓取零新增 |
| 对外提供自定义条数的 RSS | `rss.xml` 条数由 `item_limit` 决定；另生成 `rss-15/30/50.xml` 多档 |
| 不回补启用前历史 | 首次运行只收录当时 feed 里的条目，不抓历史 |
| 允许滚动更替 | 归档上限 `max_store`（默认 300），超出按时间淘汰最旧 |
| 稳定超过 10 条、目标 15+ | 归档持续累积；首次运行后每新增一次标记 +1 条，累计 5 次即超过 15 条 |

## 五分钟部署

1. **新建公开仓库**：名称建议 `douban-relay`（公开仓库的 Actions 与 Pages 才免费）。若不想公开，见文末「私有仓库」说明。
2. **上传文件**（保持目录结构）：
   ```
   douban-relay/
   ├── relay.py
   ├── config.json
   └── .github/workflows/relay.yml
   ```
3. **配 `DOUBAN_UID`**（二选一，推荐第一种）：
   - 不改文件：`Settings → Secrets and variables → Actions → Variables` 新建 `DOUBAN_UID`，值为豆瓣数字 ID（打开 `https://www.douban.com/people/你的ID/`，地址栏那串数字）。`site_url` 无需填写，会自动推导。
   - 或本地改 `config.json` 的 `douban_uid`（会写进 git 历史，公开仓库不建议）。
4. **开启 Pages**：仓库 `Settings → Pages → Build and deployment → Source` 选 **GitHub Actions**。
5. **手动跑一次**：`Actions → douban-relay → Run workflow`。首次运行会把当前 10 条写入归档并生成 RSS。
6. **订阅**：等 Pages 部署完成（约 1 分钟），订阅
   ```
   https://你的用户名.github.io/douban-relay/rss.xml
   ```

## 输出链接

| 链接 | 条数 |
|---|---|
| `/rss.xml` | `item_limit`（默认 30） |
| `/rss-15.xml` | 15 条（贴合「15 条以上」目标） |
| `/rss-30.xml` | 30 条 |
| `/rss-50.xml` | 50 条 |

改 `item_limit` 或 `presets` 后，下次运行会自动重生成（无需等新增条目）。

国内访问 `github.io` 若偏慢，可换 jsDelivr 镜像（缓存约 12 小时，可手动刷新）：

```
https://cdn.jsdelivr.net/gh/你的用户名/douban-relay@main/feed/rss.xml
# 刷新缓存：https://purge.jsdelivr.net/gh/你的用户名/douban-relay@main/feed/rss.xml
```

## 参数说明

`config.json` 只提供默认值；同名**环境变量**（可来自 GitHub Secrets / Variables）优先级更高。详见下一节。

| 配置键 | 环境变量 | 含义 | 默认 |
|---|---|---|---|
| `douban_uid` | `DOUBAN_UID` | 豆瓣数字 ID，只填数字 | 必填 |
| `site_url` | `SITE_URL` | Pages 站点地址，用于 `atom:link self` | 留空则按 `owner/repo` 自动推导 |
| `item_limit` | `ITEM_LIMIT` | `rss.xml` 的输出条数 | `30` |
| `presets` | `PRESETS` | 额外生成的固定档位，逗号分隔 | `[15,30,50]` |
| `max_store` | `MAX_STORE` | 归档上限，超出淘汰最旧 | `300` |
| `feed_title` | `FEED_TITLE` | 输出 RSS 的标题 | 见配置 |
| `feed_description` | `FEED_DESCRIPTION` | 输出 RSS 的描述 | 见配置 |

## 配置注入：不改 config.json 也能部署（公开仓库推荐）

优先级：**环境变量（Secrets / Variables） > `config.json` > 内置默认**。因此仓库里可以只留占位值，真实配置在运行时注入。

### 三步启用

1. `Settings → Secrets and variables → Actions → Variables`（或 `Secrets`）→ **New**，名称填 `DOUBAN_UID`，值为豆瓣数字 ID（打开 `https://www.douban.com/people/你的ID/`，地址栏那串数字）。
2. `config.json` 保持原样（`"douban_uid": ""`），不需要改。
3. `Actions → douban-relay → Run workflow`。未配置时会在「校验 DOUBAN_UID」步骤直接失败，并提示去哪配。

`site_url` **不需要配**：workflow 会传入 `GITHUB_REPOSITORY`，脚本自动推导为 `https://<owner>.github.io/<repo>`；用自定义域名时才需要设 `SITE_URL`。

### 该用 Variable 还是 Secret

`douban_uid` 不是凭证，用 **Variable** 更合适（可读、可在页面核对、排错方便）。

**关键事实：Secrets 挡不住 uid 暴露。** 生成的 `rss.xml` 里，`channel/link` 与每条 `guid` 都含 `www.douban.com/people/<uid>/interests/<id>`——订阅者一眼就能看到。Secrets 只能把它挡在**源码与 git 历史**之外，不能挡住输出。想让 uid 彻底不出现在 feed 里，得关掉 guid 透传（代价是下游无法与原 feed join），这属于产品取舍，不是安全措施。

### 公开部署的安全注意事项

| 事项 | 说明 |
|---|---|
| 真正的暴露面是归档 | `data/items.json`、`feed/*.xml` 会公开你的书影音标记历史，比 uid 敏感得多。介意就别公开仓库 |
| Actions 日志公开可读 | 日志对所有匿名用户可见。本项目只打印 uid 位数，不做回显；调试时别 `echo` 变量 |
| Variables 不是密文 | 公开仓库的 Variables 任何访问者都能在页面上看到；要"少一层可见"才用 Secret |
| git 历史不可撤回 | 一旦提交过真实 uid，改文件也删不掉旧提交；必要时 `git filter-repo` 或重建仓库 |
| fork PR 读不到 Secrets | 这是默认行为，本项目也没有使用 `pull_request_target`，无需额外加固 |
| 改配置不用改代码 | 改 Variable/Secret 后下次运行即生效，不产生提交，也不会把值写进历史 |
| 账号关联 | 公开仓库等于把 GitHub 账号与豆瓣账号公开绑定，这是社交层面的隐私，不是凭证泄露 |

## 时间线预期（重要）

- **启用当下**：归档最多 10 条（豆瓣窗口上限），这是「不回补」的必然结果。
- **之后每标记一次**（想看/看过/想读/读过…）：+1 条。
- **达到 15 条**：需要启用后再产生 5 次新标记，通常在几天到两三周内完成，取决于你的使用频率。
- **稳定期**：归档持续增长到 `max_store` 后开始滚动淘汰，输出条数始终由你设定。

## 与「直接解析豆瓣 RSS」的字段对照（2026-10-18 实测）

用 UID `201878529` 实跑一次，把上游 feed 与中继输出逐条比对：

| 字段 | 上游豆瓣 RSS | 中继输出 | 结论 |
|---|---|---|---|
| `guid` | `.../people/{uid}/interests/{id}` | 原样透传同一个 id | 完全一致，可直接 join |
| `title` | 含「想看/看过/读过」前缀 | 原样 | 10/10 一致 |
| `link` | 条目对应 subject 页 | 原样 | 10/10 一致 |
| `description` | 含海报 `<table>` 的 HTML | 按 CDATA 语义转义后输出，解析回来内容相同 | 10/10 一致 |
| `pubDate` | GMT | +0800 | 时间点 10/10 相同，仅表示法不同 |
| `dc:creator` | 用户昵称 | 原样 | 一致 |
| `category` | 无 | 新增 `movie/book/music` + `douban:status` | 中继多给的维度 |
| 条目数 | 固定 10 | 1 ~ `max_store`，按 `item_limit` 截断 | 中继唯一的结构性优势 |

**注意 `?cat=` 已失效**：`interests`、`interests?cat=movie`、`?cat=book`、`?cat=music` 四种写法返回**字节完全相同**，说明豆瓣已取消该参数。中继因此只抓一次（省 2/3 请求），类别改由 `link` 的域名判定，收藏状态由标题前缀解析。若你的旧代码依赖 `?cat=` 分流，需要同步改掉。

## 已知边界

- **两次轮询之间新增超过 10 条会漏采**：豆瓣只保留最近 10 条，超出窗口的条目无法追回。把 `cron` 从 `*/20` 改成 `*/10` 可降低概率（代价是执行次数翻倍，公开仓库不计费）。
- **豆瓣限流**：偶发 403/超时会自动重试 3 次；连续失败时本次不写入任何文件，不会破坏已有归档。
- **条目更新不重复计入**：同一作品从「想看」改为「看过」时，若发布时间变化会作为新条目追加；若时间未变则视为同一条。
- **公开仓库 = 数据公开**：豆瓣书影音兴趣本身就是公开信息，一般无额外暴露；介意的话见下条。

### 私有仓库可行吗

- 私有仓库的 Actions 按分钟计费（免费额度 2000 分钟/月）。20 分钟一次约 2160 分钟/月，会超出；改成 30 分钟一次约 1440 分钟/月才勉强够。
- 私有仓库使用 Pages 需要 GitHub Pro。
- 结论：**个人免费使用请选公开仓库**；确实要私有，建议 30 分钟一次 + 关闭 Pages、改用 jsDelivr 无法读取私有仓库（此时需要 Cloudflare Worker 之类的动态端点，见方案报告）。

## 常见报错

**`Get Pages site failed. Please verify that the repository has Pages enabled and configured to build using GitHub Actions`**

仓库还没启用 Pages。这一步发生在「提交归档」之后，所以**数据不会丢**，只是没有发布出去。两种修法：

- 最稳：`Settings → Pages → Source` 选 **GitHub Actions**，再重跑。
- 或直接重跑：workflow 里的 `configure-pages` 已带 `enablement: true`，会在运行时尝试自动创建 Pages 站点（需要 `pages: write`，已声明）。若自动创建被拒绝（403），回到上一条手动开。

## 停止与清理

- 暂停：`Actions → douban-relay → ⋯ → Disable workflow`
- 彻底删除：删除仓库即可，归档数据随之消失（建议先下载 `data/items.json`）。

## 本地调试

```bash
pip install feedparser==6.0.11
python relay.py                              # 需先设 DOUBAN_UID 或填好 config.json
DOUBAN_UID=1234567 python relay.py           # 也可临时用环境变量
```

首次运行会在本地生成 `data/items.json` 与 `feed/*.xml`，可直接用浏览器或阅读器验证。
