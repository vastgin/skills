# 报表定义剧本

本文件是 agent 产出报表定义的作业手册。**字段名一律以 `list_report_datasets` 的实时返回为准**，
本文只讲方法与纪律（字段清单会随服务端演进，不要背下本文种的字段名当白名单）。

## 一、两个 MCP 工具

### `list_report_datasets`（无入参，只读）

返回可查数据集及其字段元数据，**不含任何数据行**：

| 字段 | 说明 |
|---|---|
| `id` | 数据集标识，即 `query_report` 的 `datasetId`。本期只有 `patent-fee-ledger` |
| `name` / `description` | 数据集名称与口径说明（进回答的"数据口径"一并交代） |
| `keywords` | 业务口语别名，用于把用户问法锚定到数据集 |
| `defaultDimensions` / `defaultChartType` | 默认视图：用户只说"来张报表"、没给维度时用 |
| `fields[]` | 字段清单（见下） |

`fields[]` 每项：

| 字段 | 说明 |
|---|---|
| `name` | **绑定用的字段名**（唯一合法取值来源） |
| `label` | 展示名（图表标签/表头） |
| `dataType` | `string` / `number` / `date` / `boolean` |
| `role` | `dimension`（维度）/ `measure`（指标） |
| `aggregations` | 指标可用聚合：`sum` / `avg` / `count` / `min` / `max` |
| `defaultAggregation` | 指标默认聚合 |
| `filterable` | **只有 `true` 的字段能进 `filters`**，否则 `INVALID_FILTER` |
| `currencyBound` | `true` 的指标**必须同时绑定 `currency` 维度**，否则 `INVALID_BINDING`（跨币种求和是错的） |
| `description` | 口径说明（人话），播报时可以用它解释口径 |

### `query_report`（只读）

入参就是报表定义：

```json
{
  "datasetId": "patent-fee-ledger",
  "chartType": "bar",
  "dimensions": ["officeCountry"],
  "colors": [],
  "measures": [{ "field": "id", "aggregation": "count" }],
  "filters": [{ "field": "status", "operator": "eq", "value": 0 }],
  "sort": [{ "field": "m0", "direction": "desc" }],
  "limit": 10,
  "options": { "title": "按国别统计待缴年费笔数" }
}
```

返回 `{success, data, error, took}`：

- `success=true` → `data = {type, dataset, view|metricView}`，`type` 为 `chart` / `metric` / `table`；
- `success=false` → `error = {code, message}`（HTTP 仍 200，**不是异常**）。

## 二、把问法翻成定义

| 用户问法 | datasetId | chartType | dimensions | measures |
|---|---|---|---|---|
| 年费按国别分布 | 年费费用清单 | `bar` | `officeCountry` | count(笔数指标) |
| 哪几年待缴最多 | 年费费用清单 | `bar` | `feeCalendarYear` | count + `filters: status=0` |
| 待缴和已缴各多少 | 年费费用清单 | `pie` | `status` | count |
| 各币种应缴金额 | 年费费用清单 | `bar` | `currency` | sum(金额指标，currencyBound) |
| 来张年费报表（没给维度） | 年费费用清单 | `defaultChartType` | `defaultDimensions` | 与默认视图匹配的指标 |
| 2026 年已缴明细 | 年费费用清单 | `table` | — | count 或金额，`filters: status=2 & feeCalendarYear=2026` |

**维度取值**（以实时元数据为准，常见项）：

| 字段 | 含义 | 口语锚点 |
|---|---|---|
| `officeCountry` | 受理局国别 | 国别 / 国家 / 中国美国日本 |
| `feeCalendarYear` | 费款所属公历年 | 年份 / 2026 年 |
| `status` | 缴纳状态 | 待缴 / 已缴 / 已放弃 |
| `currency` | 币种 | 币种 / 人民币 / 美元 |
| `feeYear` | 应缴年度（第 N 年） | 第几年 |

**指标选择**：算"多少个"用 `count`（通常绑主键字段）；算"多少钱"用 `sum` 绑金额字段，
但**金额指标多为 `currencyBound=true`，必须同时把 `currency` 放进 `dimensions` 或 `colors`**。

## 三、排序与 limit（0208 纪律）

- 只有用户明确表达排序意图才给 `sort`：`降序` / `从高到低` / `最多` / `排名前 N` / `Top N`。
- `sort.field` 必须已经出现在本次 `dimensions` / `colors` / `measures` 里；`direction` 取 `asc` / `desc`。
- 用户点名"前 N 项"→ 同时给 `limit: N`。
- 用户没表达排序意图 → `sort` 留空，**不要替用户挑一个排序字段**，那是替他决定口径。

## 四、失败码与修正（照 `error.message` 改，不要绕过）

| `error.code` | 含义 | 怎么修 |
|---|---|---|
| `DATASET_NOT_FOUND` | 数据集不存在 / 不在本期白名单 | 用 `list_report_datasets` 的 `id` |
| `UNSUPPORTED_CHART` | 图表类型不合法 | 换合法 chartType（`metric-card` / `table` / `bar` / `line` / `pie` / `heatmap` 等） |
| `INVALID_BINDING` | 维度 / 指标数量不满足该图表类型的约束 | 按 `message` 调整维度或指标个数；金额指标补 `currency` 维度 |
| `INVALID_FILTER` | 过滤字段不在白名单或操作符非法 | 只用 `filterable=true` 的字段；操作符取 `eq/ne/in/notIn/gte/lte/gt/lt/between/contains` |
| `UNKNOWN_FIELD` | 绑定的字段不存在 | 字段名抄自 `fields[].name` |
| `INTERNAL` | 服务端内部错误 | 不要反复重试刷屏，如实告知"服务端暂时算不出来" |

**重试纪律**：同一问法最多按 `message` 修正 **1 次**。第二次仍失败就如实报错，
不要不断换字段试探（那会把"算不出来"包装成"随便给你个数"）。

## 五、播报口径

- 播报时**同时给出**：问的是什么、用的是哪个数据集、口径是什么（用数据集 `description` 与
  字段 `description`）、取的什么时候的数。
- `took` 是服务端耗时，不用播报。
- `dataset.truncated=true` → 必须说"结果被截断，不是全量"，**不得**据此做合计或家数陈述。
