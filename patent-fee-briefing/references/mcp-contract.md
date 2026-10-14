# MCP 契约与故障处置

本技能只依赖 SaaS 平台 MCP Server 的两个工具：`check_token`（预检）与 `get_fee_weekly_report`（取数）。
平台 MCP 端点与凭证来自 `~/.workbuddy/mcp.json`（server 名 `IPBuddy-Server`），
与其他 vastgin 技能共用同一份配置。

## 工具 1：`check_token`

| 项 | 值 |
|---|---|
| 入参 | 无 |
| 返回 | `{valid: bool, tenant: number\|null}` |
| 所需 scope | 无（鉴权预检本身） |

`valid=false` 即停：**不要**继续取数，先引导用户到 SaaS「MCP接入凭证」页重新生成 token 并更新
`~/.workbuddy/mcp.json`。

## 工具 2：`get_fee_weekly_report`

| 项 | 值 |
|---|---|
| 入参 | 无 |
| 所需 scope | `patent:fee:read` |
| 限流 | 120 次 / 60 秒（超出返回可读的中文提示） |
| 语义 | 与管理后台年费周报**同一份数据**，只是渲染成终端 markdown |

### 返回结构

| 字段 | 类型 | 说明 |
|---|---|---|
| `dataDate` | string | 数据基准日（服务端统一的「今天」），`yyyy-MM-dd` |
| `totalPayable` | number | 待缴年费总行数（含无截止日的行，与台账「全部」卡同口径） |
| `buckets` | array | 固定四项，顺序：逾期 / 7 日内 / 8-30 日 / 31-90 日 |
| `buckets[].key` | string | `OVERDUE` / `DUE_7` / `DUE_8_30` / `DUE_31_90` |
| `buckets[].label` | string | 与邮件指标卡同措辞：`逾期年费` / `7日内到期` / `8-30日到期` / `31-90日到期` |
| `buckets[].billCount` | number | 该档待缴行数（空档位是 `0`，不是 `null`） |
| `totalItems` | number | 90 日内待缴明细**真实**条数（截断前） |
| `shownItems` | number | 正文里**实际呈现**的明细行数（≤ 20） |
| `format` | string | 固定 `terminal-markdown` |
| `markdown` | string | 终端正文：标题 + 指标行 + 7 列明细表 + 截断提示 / 空态 |

`markdown` 明细表列序固定：`截止日期 | 专利号 | 专利名称 | 国别 | 费用项 | 应缴金额 | 状态`。
「状态」列是文字（`逾期 N 天` / `剩 N 天`）——终端没有标色能力，轻重缓急靠文字承载。

**不与 `list_fee_ledgers` 混用**：`list_fee_ledgers` 是费用清单的原始分页（平台计算引擎的只读输出），
口径与档位收敛都与周报不同。回答"要交哪些年费"一律用本工具。

## 故障处置

| 现象 | 判据 | 处置 |
|---|---|---|
| 凭证失效 | `check_token` 返回 `valid=false` | 停；引导重新生成 token 更新 `mcp.json` |
| scope 不足 | 工具报无权限 / 403 | 该凭证未授予 `patent:fee:read`，引导在「MCP接入凭证」补授权 |
| 限流 | 返回「查询过于频繁，请稍后再试」 | 稍等再试；不要连续重试刷屏 |
| 502 / 503 / 504 | 网关瞬时故障（`check_token` 正常即证明服务端可用） | 脚本已自动退避重试 4 次；仍失败则如实告知"服务端暂时不可用" |
| 返回空明细 | `markdown` 只有「本期无 90 日内待缴明细」 | **正常结果**，照实播报，不要当错误 |
| 数字与用户预期不符 | — | 以工具返回为准；用户要核对请引导到后台「年费周报 › 内容」页（同一份数据） |

## 安全

- token 只从 `~/.workbuddy/mcp.json` 内联读取，进 `Authorization` 头；**不打印、不落盘、不进日志**。
- 该文件与任何含 token 的文件都必须留在 `.gitignore` 里，**禁止提交**。
- 禁止把 token 粘贴进对话正文；需要排查时只确认"是否已配置"，不确认其值。
