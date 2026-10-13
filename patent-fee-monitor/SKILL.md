---
name: patent-fee-monitor
description: "专利年费监控：采集国家知识产权局案件查询平台的应缴年费数据，推送到用户的 SaaS 年费监控产品（MCP）。浏览器操作依赖 BrowserSkill，未安装时引导用户安装。"
user-invocable: true
---

# 专利年费监控（patent-fee-monitor）

从用户处获取已授权专利申请号清单 → 引导用户登录 CNIPA 案件查询平台 → 逐条采集著录、授权公告日、未缴年费（前 2 条）与已缴年费（前 2 条）→ 经 MCP 推送到 SaaS。

## 前置依赖：BrowserSkill（硬性）

本技能**所有页面操作必须经 BrowserSkill**（技能名 `browser-skill`，命令 `bsk`）完成：驱动用户本人已登录的 Chromium，走 DOM 读页面。**禁止绕过**——不直连 cpquery 内部接口、不用 curl/requests 伪造请求、不换其它浏览器后端。

任务开始先做一次可用性检查，未通过则**引导用户安装**，装好前不进入采集：

| 检查项 | 命令 | 通过判据 |
|---|---|---|
| CLI 在位 | `bsk --version` | 打印版本号（`bsk` 在 `~/.local/bin`，不在默认 PATH） |
| 环境就绪 | `bsk doctor` | `extension connected` 与 `browser protocol compatible` 均为 ok |
| 技能在位 | `bsk install-skill --list` | WorkBuddy 行显示 `skill installed` |

安装引导（分步命令、商店链接、话术、常见故障）见 `references/browser-skill-setup.md`。

## 步骤索引

| Step | 做什么 | 读哪份 |
|---|---|---|
| Step -1 | 检查 BrowserSkill 可用（`bsk --version` / `bsk doctor`）；不可用则引导安装，装好前不采集 | `references/browser-skill-setup.md` |
| Step 0 | 验 token（获取方式见下），调 MCP `check_token`，失败即停（不采集） | `references/data-schema.yaml` |
| Step 1 | intake：收申请号清单（文件/粘贴），归一后复述条数给用户确认 | — |
| Step 2 | 用 BrowserSkill 打开入口页，按话术引导用户完成 SSO 扫码+滑块，判定登录态 | `references/login-guide.md` |
| Step 3 | 采集：用 `references/scripts/collect_one.py`（单条）或 `collect_batch.py`（批量，默认 10 条/批）跑完查询→判定 queryStatus→提取著录/授权公告日/费用并翻译成契约编码；**分批完成即转述进度** | `references/query-playbook.md` |
| Step 4 | 归一 payload 并调 MCP `push_fee_batch`（用 `references/scripts/push_mcp.py`），按 rejected 明细补推 | `references/data-schema.yaml` |
| Step 5 | 对账汇总：按分类逐条告知用户（模板见 playbook 末尾） | `references/query-playbook.md` |
| 异常 | 412/掉线/空结果/弹窗拦截，分级处置 | `references/troubleshooting.md` |

## 红线（违反即停）

- **禁止编造费用数据**：页面上没有的字段就是没有，缺什么如实报什么，禁止凑数。
- **token 只存在于 `Authorization` 头**：禁止写入 git 跟踪的文件、日志、payload、对话正文，禁止打印/复述其值。获取方式见 `references/token-setup.md`（**禁止让用户粘贴 token 进对话**）。
- **浏览器操作只经 BrowserSkill**：所有页面读取与交互必须通过 `browser-skill`（`bsk`）驱动用户真实登录的浏览器。**禁止绕过**它直连 cpquery 内部接口、伪造请求或改用其它浏览器后端。BrowserSkill 不可用时**先引导安装**（`references/browser-skill-setup.md`），**不要**降级成别的手段硬凑，也不要在未装好的情况下谎称已采集。
- **频率上限**：每批 ≤50 条申请号；申请号用 `bsk fill` 直接填充（须回读校验），动作间随机停顿；超出清单拆多批，批间明确告知用户。
- **状态判定**：不确定时归 `error`，禁止误判 `not_granted`（error 可重试自愈，误报未授权误导用户）。
- **掉线不自动重试**：URL 回到 SSO 域即停，引导用户重新登录（见 login-guide）。
- **feeYear 两级兜底都失败**：该笔费用整笔丢弃，禁止传 null（SaaS 必 400）。
- **长任务必须分批播报进度**：禁止把整批采集塞进一次命令调用（输出会被折叠，用户全程零感知）。用 `collect_batch.py` 按批调用（**默认一批 10 条**），每批完成即转述该批进度行，**失败条目也必须播报**。
- **禁止猜测专利类型**（`patent_type` / payload 的 `type`）：它同时是 `fee_rule` 的查表键，错值会让年费规则**整体错配**，比留空更危险。客户端**只按归一后申请号第 5 位推导**（`1→发明` / `2→实用新型` / `3→外观设计` / `8→发明(PCT)` / `9→实用新型(PCT)`）；推导不出就**记入采集结果的 `meta.problems` 并停下确认**，禁止用默认值或中文类型名兜底。该字段曾因服务端漏列导致整批回滚，现已修复——来龙去脉与当前传值口径见 `data-schema.yaml` › `serverGap` / `typeFieldNote`。

## 交付边界

**采什么**（2026-10-10 起）

| 来源 | 取什么 | 规则 |
|---|---|---|
| 搜索**结果列表** | 授权公告日 | 随 patent 上报为 `grantDate`（详情页没有这个字段） |
| **应缴费信息**表 | 未缴年费 | 按**缴费截止日升序**取**前 2 条** |
| **已缴费信息**表 | 已缴年费 | 只取年费类（**不含**年费滞纳金）→ 按**缴费日期降序**取**前 2 条** |
| **应缴滞纳金信息**表 | 滞纳金阶梯 | 挂到**当前欠费年度**那一行的 `lateFeeLadder`（该表整案级、不含年度，按「当前年费金额」匹配） |

- **不采集**：收据发文信息、冲红信息、以及申请费/实审费/印花税/著录事项变更费/副本证明费等**非年费**费用种类。
- **「应缴费信息」表内已缴清的整行丢弃**：`费足`（＝已缴费）、`已缴`、`已缴费` —— 无应缴义务，不推送、不计失败。
- **已缴年费改从「已缴费信息」表单独采**（v5 前是丢弃的）。该表**没有「缴费截止日」列**，故已缴行 `lateDueDate` **留空、禁止编造**，排序与 `dedupKey` 用**缴费日期**。
- **费用状态只用服务端枚举**：未缴行 `UNPAID`/`DEFERRED`/`REDUCED`，已缴行 `PAID`。页面表外状态**整行丢弃，禁止改写凑合**。
- **已缴行能否入库由服务端决定**：系统里已有该年度行 → 置为已缴；没有 → 「已缴不建行」丢弃（计入 `filtered`，**不是错误、无需补推**）。
- **`paidTime`（缴费日期）与 `voucherNo`（票据号码）都是服务端字段、均可入库**（勿自造 `payDate`/`invoiceNo` 之类的别名）。**2026-10-13 起采集端直接产出 `voucherNo`，与上报契约同名、无需改名映射**；`push_mcp.py` 仍兼容读旧结果的 `invoiceNo`。注意：服务端对**已缴行幂等**，不覆盖已有 `paidTime` —— 验证字段能否写入必须用**待缴行**（推 `PAID`）；且改回 `UNPAID` 时只改 `status`、**不清**已缴字段。
- **字段名与语义一律以 `push_fee_batch` 的 MCP 工具 schema（服务端 DTO）为准**，禁止拿 `list_fee_ledgers` 的返回列名做对照 —— 那是平台计算引擎的**只读输出**（`dueDate`/`payableAmount`/`lateFee` 均由服务端自行推算，与上报字段不同源）。台账只用于验证「字段是否真正落库」。
  - FeeItem 的 10 个字段在 DTO 中**全部 required**（含 `paidTime`/`voucherNo`）；`push_mcp.py` 对未缴行**显式补空串**以满足契约形状（实测服务端当前未强校验，但不代表省略安全）。
  - `type` 必须是 **int 字典码**（1 发明/2 实用新型/3 外观设计），不是 `INVENTION` 这类字符串枚举名。
- 采集完成后**不要**自动发起下一轮监控；补采清单（`RETRYABLE:` 案件）需用户确认后再跑。
