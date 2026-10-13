# scripts —— 采集与上报的可执行实现

对应 `SKILL.md` 的 Step 3 / Step 4。

## v5 快速路径（推荐，2026-10-10 实测）

| 脚本 | 作用 |
|---|---|
| `collect_one.py` | **单条端到端采集**：填号 → 查询 → 开详情 → 取著录 + 费用表，一次调用跑完，输出一行进度 + JSON |
| `collect_batch.py` | 批量入口：读清单逐条调 collect_one，**逐条打印进度**（不静默） |
| `push_mcp.py` | 采集 JSON → payload → **直连 MCP 端点**上报 |

### 为什么快（旧实现慢在哪）

| 瓶颈 | 旧实现 | v5 |
|---|---|---|
| 页面读取 | `bsk observe` 快照文本 + 正则，单条 10+ 次、每次 1–3s | `bsk evaluate` 直读 DOM，**单次往返 ~130ms** |
| 等待方式 | shell `sleep` 硬等，每步 2–4s | 页面内 `await` 轮询（`evaluate` 支持 Promise），**点击→等渲染→提取压进一次调用** |
| 调用次数 | 每条 20+ 次 agent 往返 | 每条 **1 次**脚本调用 |

实测：**单条 2–9 秒**（页面缓存后约 2s），旧实现同一条要数分钟。

### 用法

**macOS / Linux**

```sh
export PATH="$HOME/.local/bin:$PATH"
export BSK_AUTO_START=0
export BSK_HOME=<daemon home>          # 用 lsof -U | grep bsk 确认，不要猜
PY=python3                             # 用本机实际的解释器

# 单条（推荐：调用方逐条调用、逐条播报）
$PY collect_one.py 2018111401422 --outdir out

# 批量（默认一批 10 条；进度逐行打印，不静默）
$PY collect_batch.py list.txt --outdir out --start 1     # 第 1–10 条
$PY collect_batch.py list.txt --outdir out --start 11    # 第 11–20 条
$PY collect_batch.py list.txt --outdir out --limit 0     # 全量一次跑完

# 上报（可先 --dry-run 看 payload）
$PY push_mcp.py --dir out [--dry-run]
```

**Windows（PowerShell）**

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"   # install.ps1 装在这里
$env:BSK_AUTO_START = "0"
$env:BSK_HOME = "<daemon home>"            # 见下方"平台支持"

python collect_one.py 2018111401422 --outdir out
python collect_batch.py list.txt --outdir out --start 1
python push_mcp.py --dir out --dry-run
```

`SESSION` / `MAIN_TAB` 可省略 —— 脚本会自动从 daemon 状态取当前会话与 agent 标签页；
多任务并存时有歧义再显式指定。

### 平台支持（Windows / macOS / Linux）

| 项 | 情况 |
|---|---|
| **bsk CLI** | 三平台官方支持。Windows 用 `irm https://raw.githubusercontent.com/Tencent/BrowserSkill/main/install.ps1 \| iex`，装到 `%USERPROFILE%\.local\bin\bsk.exe`（可用 `BSK_INSTALL_DIR` 覆盖），并自动写入用户 PATH 与 Git Bash 的 `~/.bashrc` |
| **浏览器扩展** | Chrome 商店 / Edge 外接程序都有；Edge 用 Edge 商店版 |
| **采集脚本** | `collect_one.py` / `collect_batch.py` / `push_mcp.py` / `make_report.py` 是**纯 Python + 页面 JS**，跨平台。`find_bsk()` 已处理 Windows 的 `bsk.exe` 后缀与 PATH |
| **`run_quiet.sh`** | bash 脚本。macOS / Linux 直接用；Windows 在 **Git Bash** 里跑（install.ps1 已配好 `~/.bashrc`），或**直接调 `collect_batch.py`**（推荐，纯 Python 无依赖） |
| **解释器/路径** | 脚本内**不写死**绝对路径：`run_quiet.sh` 自动探测 `python3`/`python` 并按自身位置定位同级脚本；需要时用 `PY=/path/to/python` 覆盖 |
| **无平台专属文件** | 早期用于调试的 `focus_guard.swift` / `focus_watch.swift`（macOS 专用、非采集流程）已删除 |

> **Windows 找不到 daemon home？** macOS 的 `lsof -U \| grep bsk` 在 Windows 不可用。
> Windows 下 daemon home 默认是 `%USERPROFILE%\.bsk`（即 `$HOME\.bsk`），
> 用 `Get-ChildItem $HOME\.bsk` 确认；`bsk status` 能正常输出即可不必显式设置 `BSK_HOME`。

### Agent Window：怎么做到「不反复弹窗、不抢焦点」（2026-10-13 更新）

bsk 每次 `session start` 都会**新开一个 Chrome「Agent Window」** —— 反复新建就是反复弹窗，
且**默认会抢走前台焦点**（用户明确反馈：希望启动后能最小化、腾出屏幕做别的事）。

**bsk 0.3.2 已原生支持 `--no-focus`**，`ensure_session()` 已内置：

| 做法 | 说明 |
|---|---|
| **优先复用会话** | `ensure_session()` 有存活会话就绝不 start —— 窗口只开一次 |
| **`session start --no-focus`** | 必须新建时带这个参数，窗口在**后台打开、不抢焦点**。⚠️ 手动执行 `bsk session start` 时漏掉它就会直接抢焦点（踩过的坑） |
| **daemon 常驻** | 会话存活的前提。daemon 是**后台进程、不弹窗**；**杀掉 daemon 才会导致会话丢失、下次重新开窗** |
| **默认保留会话** | 采集结束不关窗口，下次直接复用、不再弹 |
| **`--close-when-done`** | 想采完立刻清场就加这个参数，会关闭会话及其 Agent Window |
| **`bsk session stop --all`** | 手动一键清场 |

命令行会打印本次是「新建 Agent Window」还是「复用现有窗口（不新开）」，一眼可辨。

**窗口缩小 = 不挡事，效果等同最小化**（实测 2026-10-13）：

```sh
bsk window resize --session <sid> --width 420 --height 320
```

在 420×320 下采集，费用明细、专利名称、授权公告日、法律状态与全尺寸**逐项完全一致**，
单条耗时 2.4s（与全尺寸持平）。**不要用 100×100** —— 过小会让页面布局崩坏、选择器失效。

> bsk 仍**没有** hide/minimize 子命令；macOS AppleScript 自动化权限默认未授予
> （`osascript` 报 `-10004`），因此不要依赖 `osascript` 去最小化窗口。
> 要真正「完全不出现」仍需用户手动最小化，但**缩到420×320 摆在角落已足够不干扰**。

**一键静默采集**：`run_quiet.sh` 把上述三点（复用会话 / `--no-focus` / 缩窗口 / 结束恢复）
串成一步，且退出时自动还原窗口尺寸：

```sh
./run_quiet.sh list.txt out 10    # 清单、输出目录、每批条数
```

脚本只复用或后台新建会话、缩小窗口，**全程不抢焦点**，适合用户腾出屏幕做别的事。

### ★ 运行期抢焦点的根因修复：详情页改为「同标签页」打开（2026-10-13）

上面 `--no-focus` 只管**建会话那一刻**。真正打断用户的凶手是**运行期**：
站点点击案件标题时用 JS **另开标签页**打开详情页，这会让 Chrome 窗口冲到前台，
每采一条就把用户打断一次。

**修法（已落地在 `collect_one.py`）**：点击标题前把页面的 `window.open` 覆盖成
「当前标签页跳转」（`PATCH_WINDOW_OPEN_JS`），于是详情页在**同一个标签页**里加载
—— 不新建标签、不激活窗口、**根本不产生抢焦点**。

- 为什么不能改 `target="_self"`：实测无效，说明站点是**显式调 `window.open`**。
- 为什么保留「新标签页」分支（`wait_detail` 返回 `how='new'`）：万一站点改版，仍能靠
  兜底路径采到数据（读完关掉该标签）。
- **配套坑**：同标签页模式下主标签 URL 也会含 `/detail/`，`detail_tabs()` 必须用
  `exclude_main` 排除主标签，否则 `close_detail_tabs()` 会把主标签关掉。

**实测验收（2026-10-13）**：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 采集 10 条期间的焦点切换 | 频繁（每开一次详情页抢一次） | **0 次**（100s 监控期内前台始终未变） |
| agent 标签页数量 | 每条新增 1 个详情标签 | **恒为 1 个** |
| 数据一致性 | — | 与旧模式**逐项完全一致**（费用/名称/授权日/法律状态/票据号码/滞纳金阶梯） |
| 单条耗时 | 2–20s（偶发重试抖动） | 4.6–5.5s，**更稳定** |

> ❌ **已废弃并移除的两个调试脚本**：
> - `focus_guard.swift` —— 在 Chrome 抢走焦点后再抢回来。属于**反复夺取**，
>   用户明确否决，已删除。
> - `focus_watch.swift` —— 仅用于**验证**「是否还会抢焦点」的 macOS 专用诊断工具
>   （依赖 AppKit/Swift，Windows 上无法运行）。修复已验证完毕，作为交付物属多余，已删除。
>
> 二者都**不属于采集流程**，删除不影响任何功能。

### 采集规则（2026-10-10 更新）

| 项 | 规则 |
|---|---|
| **授权公告日** | 从**搜索结果列表行**取（行文本含 `授权公告日：YYYY-MM-DD`），随 patent 上报为 `grantDate` |
| **未缴年费** | 「应缴费信息」表 → 按**缴费截止日升序**取**前 2 条**（`feeStatus=UNPAID`） |
| **已缴年费** | 「已缴费信息」表 → 只取年费类（不含年费滞纳金）→ 按**缴费日期降序**取**前 2 条**（`feeStatus=PAID`） |

**关键事实：国知局「已缴费信息」表没有「缴费截止日」列**（列序是
`费用种类 | 应缴金额 | 缴费日期 | 缴费人姓名 | 票据代码 | 票据号码`）。
因此已缴行的 `lateDueDate` **留空**（不编造），排序与 `dedupKey` 都基于页面唯一的日期字段
**缴费日期**。该口径已与用户确认。

被截断的条数记在 `meta.truncated`（如「未缴年费 11 条中仅取截止日最早的 2 条」），便于对账。

### 已缴行的上报字段（字段名以 **`push_fee_batch` 的 DTO schema** 为准）

| 页面字段 | 采集 JSON | payload 字段 | 实测结果 |
|---|---|---|---|
| 票据号码 | `voucherNo` | `voucherNo` | ✅ **可入库**（传 `0011886797`，台账即为该值） |
| 缴费日期 | `paidTime` | `paidTime` | ✅ **可入库**（传 `2026-06-24`，台账存为 `2026-06-24T00:00:00`） |

两个都是服务端**真实字段名**，采集端与 payload **完全同名、无需映射**（2026-10-13 起统一）。
**不要自造 `invoiceNo`/`payDate` 之类的别名** —— 服务端没有这些字段。
页面列名叫「票据号码」，但契约叫 `voucherNo`，**以契约为准**（与其余字段口径一致）。
`push_mcp.py` 仍兼容读取改名前旧结果的 `invoiceNo`/`payDate`，新采集不再产出。
`paidTime` 传「纯日期」或「ISO 日期时间」都可以，服务端统一按日期存储（时间部分归零）。

> **踩过的坑（重要，别再犯）**：曾误判「`paidTime` 传不进去、疑似只读」——
> 原因是**拿已缴行反复测**。服务端对**已缴行做幂等保护：不覆盖已有的 `paidTime`**，
> 所以怎么测都像「没生效」。**要验证一个状态字段能否写入，必须用「会发生状态跃迁」的行**
> （如拿待缴行推 `PAID`）。换成待缴行一测即通。
>
> 另一个副作用：`feeStatus` 改回 `UNPAID` 时服务端**只改 `status`，不清 `paidAmount`/`paidTime`/`voucherNo`**，
> 这些残留值**无法通过 MCP 清空**（传空串也无效），只能从 SaaS 后台处理。测试时注意别拿真实案件当靶子。

### 已缴行会不会入库，取决于系统里「有没有那一年的行」

服务端对 `feeStatus=PAID` 行的处理规则（实测 + 工具文档一致）：

- 系统里**已有**该年度的费用行 → 把该行**置为已缴**（`status: 0 → 2`，写入 `paidAmount`）。
- 系统里**没有**该年度的行 → **「已缴不建行」直接丢弃**，计入 `filtered`（**不是错误，无需补推**）。

实测样例（`2018111401422`，推了第 10/11 年未缴 + 第 9/8 年已缴）：

```
received=4  accepted=3  filtered=1
  第10年 UNPAID → accepted（新建）
  第11年 UNPAID → accepted（新建）
  第 9年 PAID   → accepted（系统已有该行 → 置为已缴）
  第 8年 PAID   → filtered（系统里没有第 8 年的行 → 已缴不建行）
```

**含义**：采集端按规则取「最近 2 条已缴年费」是照做，但其中**不一定都入库** ——
只有系统里存在对应年度行的那些才会生效。对账时不要把这个 `filtered` 当失败。

> ⚠️ **纠正一个曾导致误判的做法（2026-10-13）**
>
> 旧文档建议「调 `list_fee_ledgers` 看返回列名，反推payload 字段名」——**这个方法是错的**。
> 台账是平台计算引擎的**只读输出**，工具描述已写明「金额与状态由平台计算引擎维护，
> 回传数据不影响清单」。台账里的 `dueDate` / `payableAmount` / `lateFee` / `lateFeeOfficial`
> 都是服务端按规则**自行推算**的，与上报字段**不同源**（实测台账 `dueDate` 由申请日+规则算出，
> 和页面「缴费截止日」不是一个口径）。
>
> **正确做法**：字段名与语义一律以 **`push_fee_batch` 的 MCP 工具 schema（服务端 DTO）** 为准
> —— 它由 DTO 自动生成，就是上报契约本身。`list_fee_ledgers` 只在验证
> 「某个字段是否真正落库」时有参考价值，**不能用来定义上报契约**。

### 上报

服务端会零星返回 **502 Bad Gateway**（瞬时故障，`check_token` 正常即证明服务端可用）——
`push_mcp.py` 已对 502/503/504 自动退避重试 4 次，不要把这种抖动当成批次失败。

**「接受」≠「入库」**：服务端对 DTO 里没有的字段**静默忽略**（不报错、不计 rejected）。
验证新字段时，务必推完再调 `list_fee_ledgers` 核对回值，否则会以为传成功了。

### 退出码（collect_one.py）

`0`=ok · `1`=not_found · `2`=not_granted · `3`=error(可重试) · `4`=登录态失效 · `9`=会话不可用

---

## 实测踩坑（改脚本前必读）

1. **详情页是 Element UI，树按需渲染**。同一时刻只有一个面板在 DOM 里 ——
   著录（发明名称/申请日/申请人）必须在**切到「费用信息」之前**取，否则文本里只有费用表。
   旧脚本按 Quasar (`q-*`) 选择器写，已不适配。
2. **JS `element.click()` 是合成事件，站点不认**，不会打开详情新标签（按钮点击却是有效的）。
   打开详情必须走 `bsk click` 派发真实鼠标事件。
3. **`bsk fill` 必用**（真实事件）。用 JS `HTMLInputElement.prototype.value` setter 会被前端受控组件忽略，
   表现为 `FILL_FAIL`。
4. **登录态中途失效**会弹出统一身份认证弹窗（`L1 modal cover=100%`）；脚本已识别为
   `LOGIN_REQUIRED`（rc=4），交由调用方走 `request-help` 重新登录后重跑该条。
5. **MCP 工具 `push_fee_batch` 的数组参数过不去**：`patents`/`fees` 无论怎么写都报
   `/payload/patents: must be array`（空数组也失败）。这是工具调用层的序列化缺陷，
   故 `push_mcp.py` 直连 `http://localhost:48080/open-api/mcp`（`initialize` → `tools/call`）。
   token 只从 `~/.workbuddy/mcp.json` 内联读取，不打印、不落盘。
6. **`bsk tab close` 的 tab id 是位置参数**（`bsk tab close --session <sid> <TAB_ID>`），
   不接受 `--tab-id`；`bsk` 无 `open` 子命令，用 `navigate`。
7. `push_mcp.py` 用 `_payload_*.json` / `push_result.json` / `meta.json` 作为产物；
   采集目录里的 debug 文件（`info_*.txt` 等）不会干扰上报（按 `applyNo` 字段识别）。
8. **滞纳金阶梯（`lateFeeLadder`）必须把中文时段拆成两个日期**：「应缴滞纳金信息」表表头是
   `缴费时间 | 当前年费金额 | 应缴滞纳金额 | 总计`，第一列是**中文时段文本**
   （`2026年10月01日到2026年10月30日`）。按列位置硬映射会把整段文本塞进 `windowStart`，
   服务端解析日期时**整批 502**（`Text '...' could not be parsed at index 4`）。
   正确做法：① 用 `到` 拆成两个中文日期并转 ISO；② `lateFee` 取「应缴滞纳金额」列、
   `total` 取「总计」列；③ 该表**不含年度信息、是整案级的** —— 用「当前年费金额」
   匹配**最早未缴年度**那一行挂载（曾错挂到所有未缴行）。
   **注意**：这个 bug 只在专利**存在滞纳窗口**时才暴露，无滞纳的案子全传 `[]`，一路静默正常。

