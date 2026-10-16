---
name: patent-search-report
display_name: 专利检索报告
display_name_en: Patent Search Report
description: "数据源：Google Patents（patents.google.com），免费、无需账号。免费专利检索与著录数据抓取，并可整理成一份专利检索报告。在用户自己的浏览器里按关键词/申请人/发明人/日期检索专利并取回公开号，再进入详情页抓取完整著录项（标题、申请人、发明人、申请号、申请日、公开日、授权公告日、分类号、同族、引用、法律状态、法律事件、摘要，可选权利要求与说明书），最后汇成检索报告（命中量级 + 清单 + 重点条目摘要 + 技术路线观察）。当用户要做专利检索、查公开号、按申请人或关键词找专利、抓取某件专利的著录信息、或要一份专利检索报告时使用。数据源为 Google Patents 的公开数据（法律状态等为 Google 推测，非法律结论）；浏览器操作依赖 BrowserSkill（bsk），未安装时引导安装。"
description_zh: "在 Google Patents 上做免费、免登录的专利检索与著录数据抓取，并整理成一份专利检索报告：按关键词/申请人/发明人/日期检索取回公开号，再进详情页抓取标题、申请人、发明人、申请号、申请日、公开日、授权公告日、分类号、同族、引用、法律状态与摘要，最后汇成检索报告（命中量级 + 清单 + 重点条目摘要 + 技术路线观察）。"
description_en: "Free, login-free patent search and bibliographic data extraction on Google Patents: search by keyword, assignee, inventor or date to get publication numbers, then pull full bibliographic records (title, assignee, inventors, application number, filing/publication/grant dates, classifications, family, citations, legal status, abstract) from detail pages, and assemble them into a patent search report (hit volume + list + key-entry summaries + technology-landscape observations)."
version: 1.0.4
user-invocable: true
agent_created: true
---

# 专利检索报告（Google Patents）

**数据源**：Google Patents（`patents.google.com`）的公开数据 —— 免费、免登录、无需账号。

在 Google Patents 上做**免费、免登录**的专利检索与取数：检索 → 拿公开号 → 进详情页 → 抓结构化著录项 → 整理成一份可交付的检索报告。

覆盖的检索维度：技术关键词、申请人/专利权人、发明人、公开号/申请号、日期区间、法律状态、文献类型。
覆盖的详情字段：标题、摘要、申请人（当前/原始）、发明人、申请号、优先权日/申请日/公开日/授权公告日、分类号（CPC/IPC）、同族与多版本、引用与被引计数、法律状态与法律事件时间线，按需可取权利要求与说明书全文。

## 能做什么

| 需求 | 用什么 |
|---|---|
| 按关键词/申请人/发明人/日期检索，拿公开号清单 | `scripts/search.py` |
| 已知公开号，抓完整著录项 | `scripts/detail.py` |
| 「检索 + 取详情」一步到位 | `scripts/collect.py` |
| 出一份检索报告（命中量级 + 清单 + 重点条目著录项与摘要 + 技术路线观察） | 先 `search.py` 拿清单，再 `detail.py` 批量取详情，最后合并成 Markdown/JSON |
| 看检索语法、字段口径 | `references/query-syntax.md`、`references/field-map.md` |

## 前置依赖：BrowserSkill（硬性）

**所有页面访问必须经 BrowserSkill**（技能名 `browser-skill`，命令 `bsk`）在用户真实浏览器里发起：

- 检索走 patents.google.com 的**同源** `/xhr/query` 接口（在页面上下文内 `fetch`，见下「为什么这么实现」）——不绕代理、不改用别的浏览器后端、不用 Python 直接发请求。
- 详情走「打开详情页 → 页面内取回该页的服务端 HTML → 解析」。
- 三条脚本都会自动完成环境探测与会话复用（优先复用**已在运行**的 daemon，不重复弹窗）。

未装好时**先引导安装**（`references/browser-skill-setup.md`），装好前不要降级成 curl/requests 硬凑，也不要谎称已取到数据。

## 快速开始

```sh
PY=<python3>                      # 任意 3.8+ 均可
S=~/.workbuddy/skills/patent-search-report/scripts

# 1) 检索：拿公开号清单（表格便于阅读）
$PY $S/search.py --query "充电桩 液冷" --country CN --limit 20 --format table

# 2) 检索：按申请人 + 时间窗 + 只要授权
$PY $S/search.py --assignee "宁德时代新能源科技股份有限公司" --country CN \
      --after priority:20230101 --status GRANT --limit 50 --out /tmp/hits.json

# 3) 已知公开号，抓详情（默认中文页；中文专利给中文标题，外文给机译中文）
$PY $S/detail.py CN114630558B --lang zh --with-claims

# 3b) 只有申请号也能用（自动换公开号，见「红线」）
$PY $S/detail.py 2018111401422 --lang zh

# 4) 一步到位：检索 → 前 N 条进详情页
$PY $S/collect.py --query "固态电池 电解质" --country CN --limit 20 --detail-limit 10 \
      --lang zh --out /tmp/report.json
```

> `--format` 支持 `json`（默认，含 total 等元信息）/ `ndjson` / `csv` / `tsv` / `table`。
> stdout 只放结果；**进度走 stderr**（形如 `[详情 3/10] CN114630558B ok · 1.2s · 一种散热系统…`）。
> **加了 `--out` 时 stdout 只给精简摘要**（元信息 + 前 10 行表格），完整结果落盘——批量跑就不会把上下文冲爆。长任务要逐条转述进度行给用户。

## 步骤索引

| Step | 做什么 | 读哪份 |
|---|---|---|
| 0 | 环境检查：`bsk --version` / `bsk doctor`；不通过则引导安装 | `references/browser-skill-setup.md` |
| 1 | intake：把用户需求翻成检索式与过滤条件（关键词/申请人/时间窗/是否只看授权）。**先把检索式复述给用户确认**，尤其是宽泛词 | `references/query-syntax.md` |
| 2 | 跑 `search.py`，把 total（命中量）与首屏结果转述给用户，让用户决定要不要收窄 | — |
| 3 | 确认名单后跑 `detail.py` / `collect.py` 取详情；分批播报进度 | — |
| 4 | 交付：默认 JSON 落盘 + 给用户表格摘要；字段含义见 `references/field-map.md` | `references/field-map.md` |
| 异常 | 命中为 0 / 拦截 / 会话丢失 / 字段为空 | `references/troubleshooting.md` |

## 为什么这么实现（踩过的坑，改之前先读）

1. **检索不要读 DOM，要走同源 `/xhr/query`。**
   Google Patents 检索页是 Polymer 应用，结果是异步渲染的，读 DOM 要等渲染、要滚动懒加载、翻页要点按钮。
   它自己取数用的就是 `/xhr/query?url=<编码后的检索式>&exp=`，返回结构化 JSON（含 `total_num_results`、`total_num_pages`、每条 patent 的号/标题/申请人/发明人/三个日期/PDF）。
   本技能在 `patents.google.com` 的页面上下文里 `fetch` 这个**同源**接口，单页实测 **≈0.7s**。

2. **详情页更不能读渲染后的 DOM——会丢字段。**
   Polymer 水合完成后会把服务端渲染的语义标记整体替换掉：实测 `document.querySelectorAll('[itemprop]')` 变成**空集**，且信息缺项（没有 Authority、没有日期、没有 Original Assignee）。
   正确做法：`navigate` 到详情页后，在该页上下文里 `fetch(location.href)` 取回**服务端渲染 HTML**，用 `DOMParser` 解析再按语义标记抽字段。单条实测 **≈0.7–1.2s**，字段齐全。

3. **`<dt>/<dd>` 不能只遍历 `<dl>` 的子元素。**
   服务端 HTML 里 Info 区被拆成多个 `<dl>`，而且有一批 `<dt>/<dd>` 直接裸露在 `</dl>` 之后（不合法但浏览器容错）。
   必须按**全文档 dt 顺序**往后吃连续的 dd 兄弟节点（见 `scripts/js/detail.js`）。

4. **标签页只用 `--scope agent`。**
   绝不用 `tab list --scope all`：那会带上用户自己的标签页，一旦误导航就把用户正在看的页面顶掉。所有操作只在自己的 Agent Window 内。

5. **会话优先复用。** `bsk session start` 会新开一个 Chrome「Agent Window」，反复 start 就是反复弹窗。`bsk_common.py` 会先找存活会话；daemon 常驻时会话可跨调用复用。采集完**默认保留会话**（下次不再弹窗），要立刻清场再手动 `bsk session stop --all`。

## 红线

- **禁止编造字段**：页面上没有就是没有，缺什么报什么。`grant_date` 是**推导**来的（法律事件 GR01 → 同族 B 文本公告日），输出里要看来源说明，别当成官方授权公告日直接下结论（见 `field-map.md`）。
- **申请号不是公开号**：`detail.py` 识别到申请号会**自动换号**（CN 现行 12 位去掉 `.校验位`、旧式 13 位去掉末位校验位，再检索取第一名），并在 `_meta.resolved_from_appno` 里留痕。命中数 >5 时会提示「可能不唯一，请人工复核」——这时**必须让用户确认**，不要默认第一条就对。要禁用换号用 `--no-resolve-appno`。
- **只做只读检索**：不代用户下载 PDF（把 `pdf_url` 给用户）、不做批量镜像、不绕过任何访问限制。
- **频率自律**：单页 ≤100 条（服务端硬上限，小于 10 按 10 处理）；翻页最多到第 100 页（Google 上限）；批量详情每条之间 ≥0.3s（脚本默认 0.3s），**不要并发**。
- **浏览器操作只经 BrowserSkill**：不改用别的浏览器后端、不伪造请求、不用 Python 直接发 HTTP。
- **不碰用户自己的标签页**：一律 `--scope agent`。
- **长任务分批播报**：不要指望一次调用里塞 50 条还让用户有感知；按批跑、每批转述进度行，失败条目也要报。

## 交付边界

- **采**：检索结果（公开号、标题、申请人、发明人、优先权日/申请日/公开日、语言、PDF 链接、同族状态）+ 详情页全部著录项（见 `references/field-map.md`）。
- **默认不采**：权利要求与说明书（体积大）——需要时加 `--with-claims` / `--with-description`；引用/被引/相似文献明细需 `--with-citations` / `--with-cited-by` / `--with-similar`（计数永远返回）。
- **不采**：Google Patents 的「Concepts / Similar」语义标签、机器翻译之外的官方译文、以及任何需要账号的功能。
- 数据源是 **Google 的公开数据**，法律状态是 Google 的**推测**（页面上有免责声明），涉及法律效力的结论必须回到官方渠道复核。
