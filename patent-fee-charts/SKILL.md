---
name: 专利年费分析图表
description: "在终端里做专利年费的数据分析：按年费年度、缴纳状态、币种、清单行来源标签等维度分组统计，取数与 SaaS 智小勤图表走完全同一条报表引擎链路。图表会降级成终端可读的表格与字符条形图。只读，不改动任何数据。"
version: 1.1.0
user-invocable: true
---

# 专利年费分析图表

用户问「年费按币种怎么分布」「哪几年待缴最多」，本技能用 **与 SaaS 智小勤完全同一条报表引擎链路**
算出同样的数，然后把图表**降级**成终端能看的形态（表格 + 字符条形图）。

**只读**：只执行报表定义，不写任何数据。

## 与智小勤的关系（重要）

| 环节 | 智小勤（页面） | 本技能（终端） |
|---|---|---|
| 语义层 | 数据集 + 字段元数据 | 同一个（MCP `list_report_datasets` 读同一份元数据） |
| 报表定义 | 模型把自然语言翻成定义 | **agent 自己**把自然语言翻成同一个定义结构 |
| 执行 | 报表引擎 | **同一个**报表引擎（MCP `query_report` → `ReportQueryApi`） |
| 渲染 | ECharts（页面） | **本技能负责**降级成表格 / 字符条形图 |

只有最后一格不同 —— 这正是本技能存在的理由。**取数口径必须一致**，渲染差异是刻意的。

## 步骤索引

| Step | 做什么 | 读哪份 |
|---|---|---|
| Step 0 | 验凭证：MCP `check_token`，失败即停 | [`../patent-fee-briefing/references/mcp-contract.md`](../patent-fee-briefing/references/mcp-contract.md) |
| Step 1 | 调 `list_report_datasets` 拿字段白名单（**不许跳过**） | `references/report-definition-playbook.md` |
| Step 2 | 按用户问法产出报表定义（datasetId / chartType / dimensions / measures / filters / sort / limit） | `references/report-definition-playbook.md` |
| Step 3 | 调 `query_report` 执行 | `references/report-definition-playbook.md` |
| Step 4 | 失败（`success=false`）时按 `error.message` 修正定义后重试，**不要**绕过 | 同上 |
| Step 5 | 把结果渲染成终端文本并播报 | `references/terminal-render-rules.md` |
| 异常 | 凭证 / scope / 限流 / 网络抖动 | `../patent-fee-briefing/references/mcp-contract.md` |

命令行入口（推荐）：

```sh
python3 references/scripts/render_chart.py --list                     # 列数据集与字段（产出定义前必做）
python3 references/scripts/render_chart.py --definition def.json      # 执行定义并渲染成终端文本
echo '<def json>' | python3 references/scripts/render_chart.py --definition -
python3 references/scripts/render_chart.py --definition def.json --json   # 只回原始 JSON，不渲染
```

## 报表定义纪律（与智小勤同源，违反即任务失败）

1. **只产出定义，不产出 SQL**；字段名只能取自 `list_report_datasets` 的 `fields[].name`，
   编造字段会被引擎以 `UNKNOWN_FIELD` / `INVALID_FILTER` 拒回。
2. **数值一律由引擎算**：不要自己求和、求平均、算增长率，也不要对返回的 `dataset.rows` 二次汇总
   去回答"总共多少"。需要合计就再发一次带相应维度/指标的查询。
3. **排序只在用户明确要求时给**：`sort` 必须绑定本次已用的维度或指标；用户点名"前 N"时同时给 `limit`；
   用户没说就留空（引擎按数据首次出现顺序展示）。
4. **不回答"意味着什么"**：只答"是什么"。同比环比、归因分析、结论建议都不做。
5. **截断必须播报**：`dataset.truncated=true` 表示不是全量，**不得**据此做合计或家数陈述。

## 红线（违反即停）

- **禁止编造数据**：字段、指标、数值全部来自工具；引擎说算不出来就照 `error.message` 如实说。
- **token 只存在于 `Authorization` 头**：不打印、不落盘、不进日志、不进对话正文。
- **不写数据**：本技能只读；上报/修改走 `patent-fee-monitor`。
- **不查域外数据集**：本期只开放年费费用清单数据集，工具会拒绝其它 `datasetId`，不要试图绕过。
- **渲染不许改变数字**：字符条形图只是视觉辅助，纵轴比例必须标注；
  终端表达不了的图型就出表格 + 说明，**不要假装画出来**（见 `references/terminal-render-rules.md`）。

## 交付边界

**覆盖的分析问法**

| 问法 | 定义要点 |
|---|---|
| 年费按币种分布 | 维度 `currency`，指标 count(id) 或 sum(金额) |
| 中国与海外各多少 | **只能用 `currency`**：数据集中没有国别列，来源也分不出国别（引擎行的中国与海外来源同为「规则」） |
| 年费按年费年度分布 | 维度 `feeCalendarYear` |
| 按缴纳状态分布 | 维度 `status`（0 待缴 / 2 已缴 / 3 已放弃） |
| 金额合计 / 按币种 | 金额类指标 **必须**同时绑定 `currency` 维度 |
| 按清单行来源分布 | 维度 `sourceLabel`（规则 / 应缴单 / 官方；派生标签，**不能进 `filters`**） |
| 某年 / 某状态的明细 | chartType `table` + `filters` |

字段全集与取值一律以 `list_report_datasets`（或 `render_chart.py --list`）的实时返回为准；
本表只是常见问法的示例，**不是白名单**——示例里没有的字段不是不存在，示例里有的字段也以工具返回为准。

**明确不做**

- 不做自由 SQL、不做跨数据集 join。
- 不导出文件、不做定时报告。
- 不替用户决定口径：用户没说维度就按数据集默认视图（`defaultDimensions` + `defaultChartType`）走。
