# 字段口径（每个字段从哪来、能不能信）

数据源是 **Google Patents 的公开数据**。Google 自己在页面上就有免责声明：法律状态、申请人、优先权日都是**推测**，不是法律结论。涉及法律效力/权利归属的结论必须回官方渠道复核。

## 1. 检索结果字段（`search.py` / `collect.py` 的 `hits[]`）

来源：patents.google.com 的 `/xhr/query` 接口（`results.cluster[].result[].patent`）。

| 字段 | 说明 |
|---|---|
| `publication_number` | 公开号，如 `CN114630558B` |
| `url` | 详情页地址（带语言段，Google 自己给的语言） |
| `title` | 标题（已去掉结果高亮 `<b>` 与 HTML 实体） |
| `assignee` | 申请人/专利权人 |
| `inventor` | 发明人 |
| `priority_date` / `filing_date` / `publication_date` | 优先权日 / 申请日 / 公开日（`YYYY-MM-DD`） |
| `language` | Google 认为的原文语言代码 |
| `country` | 从公开号前缀提取的国家/地区代码 |
| `snippet` | 命中片段（可能截断，带 `…`/`&hellip;` 已解码） |
| `pdf_url` | 全文 PDF 直链（Google 托管） |
| `country_status` | 同族各国状态，如 `[{"country_code":"CN","best_patent_stage":{"state":"ACTIVE"}}]` |

## 2. 详情字段（`detail.py` / `collect.py` 的 `results[]`）

来源：`https://patents.google.com/patent/<PUB>/<lang>` 的**服务端渲染 HTML** 里的语义标记（`itemprop` / `meta[name=citation_*]`）。

| 字段 | 说明 | 可信度 |
|---|---|---|
| `publication_number` / `kind_code` | 公开号 / 种类代码（A 申请公布、B 授权公告、U 实用新型、S 外观设计） | 高 |
| `country_code` / `authority` / `authority_name` | 受理局，如 `CN` / `China` | 高 |
| `application_number` | **Google 的写法**，把种类代码拼在末尾（`CN202210114753.XA`） | 高 |
| `application_number_core` | 去掉国家代码与末尾种类代码的本体（`202210114753.X`），便于与官方申请号写法对齐、与内部台账对号 | 中（由上面推导） |
| `representative_publication` | 授权文本号（有 B 文本时给出，如 `CN114630558B`） | 高 |
| `title` / `title_full` | 当前语言页的标题 | 高 |
| `abstract` | 摘要（跟随所选语言；`--lang zh` 时外文专利给**机器翻译**中文） | 高（注意机译） |
| `priority_date` / `filing_date` / `publication_date` | 优先权日 / 申请日 / 公开日 | 高 |
| **`grant_date`** | **授权公告日——推导值，不是 Google 的独立字段**。取顺序：① 法律事件码 `GR**`（如 GR01 Patent grant）的日期 → ② 同族里 B 文本的公开日。中国授权公告以 **B 文本公开日**为准，两者通常一致，但**必须标注它是推导来的** | **中** |
| `inventors` | 发明人（可能多名） | 高 |
| `assignee_current` / `assignee_original` | 当前/原始申请人。**Google 常只给英文名**（如 `Huawei Digital Power Technologies Co Ltd`），中文名要去检索结果的 `assignee` 或官方渠道取 | 中 |
| `legal_status` | Google 的法律状态推测（`Granted` / `Active` / `Expired` / `Pending` …）。**不同专利口径不统一**：有的给 `Granted`，有的只给 `Active` | 低（推测） |
| `ifi_status` | IFI 状态（`Active` / `Expired` 等） | 低（推测） |
| `prior_art_keywords` | Google 抽取的现有技术关键词（英文） | 中 |
| `classifications` | `[{code, desc, scheme}]`，`scheme` 为 `CPC` 或 `IPC`；含层级（`H` → `H05` → `H05K` → `H05K7/20`） | 高 |
| `citations_count` | **引用他人**的专利文献条数（backward） | 高 |
| `cited_by_count` | **被他人引用**的条数（forward） | 高 |
| `similar_count` | 相似文献条数 | 高 |
| `also_published_as` | 同族里本文献的多版本（A/B 文本） | 高 |
| `docdb_family` | DOCDB 同族公开 | 高 |
| `country_status` | 同族各国代表公开 | 高 |
| `other_languages` | 该专利还有哪些语言版本 | 高 |
| `pdf_url` | 全文 PDF 直链 | 高 |
| `url` / `lang` | 抓取的地址与语言 | 高 |
| `legal_events` | 法律事件时间线 `{date, code, title, desc}`（已按 `date+code+title` 去重；Google 服务端 HTML 里同一事件常重复两遍） | 高（事件码含义见下方） |
| `events` | 里程碑事件 `{date, title, type}`，如「Application filed by …」 | 中 |
| `claims` / `claim_count` | 权利要求全文与权项数（需 `--with-claims`；`claim_count` 取自 `Claims (N)` 标题） | 高 |
| `description` | 说明书全文（需 `--with-description`，很大） | 高 |
| `citations` / `cited_by` / `similar_documents` | 明细列表（需对应开关） | 高 |
| `_meta` | `{source, html_bytes, elapsed_s}`，用于诊断（HTML 过小 = 页面异常） | — |

### 常见法律事件码（INPADOC）

| 码 | 含义 |
|---|---|
| `PB01` | 公布（Publication） |
| `SE01` | 实质审查请求生效 |
| `GR01` | 授权（Patent grant） |
| `AN00` / `AS00` | 申请人/权利转移 |
| `CC` / `CE` | 权利终止 / 权利失效 |
| `MK` | 著录变更 |

> 事件码语义以 EPO INPADOC 标准为准；不确定时**只报码与日期，不要自行翻译成法律结论**。

## 3. 语言（`--lang`）

| 取值 | 效果 |
|---|---|
| `zh`（默认） | 中文页。CN 专利给原始中文；**外文专利给 Google 的机器翻译中文**（标题、摘要、权利要求都是机译） |
| `en` | 英文页。CN 专利的标题/摘要变成英译（申请人仍是英文名） |

**要给用户看的原文/中文标题，默认 `zh` 最合适**；要英文检索式对齐或做外文对比时用 `en`。
交付时如果用了 `zh` 但对象是外文专利，**必须说明是机器翻译**。

## 4. 交付前必须交代的口径边界

| 口径 | 事实 | 交付时怎么说 |
|---|---|---|
| 数据来源 | Google Patents 的公开数据，免登录、无账号功能 | 说明结论来自 Google Patents |
| `grant_date` | **推导值**：GR01 事件日期，或同族 B 文本公开日 | 标为「推导」，需要精确授权公告日让其回专利局官方文本核对 |
| `legal_status` / `ifi_status` | Google 的**推测**，且不同专利口径不统一 | 只作参考，不作为法律结论 |
| `assignee_current` / `assignee_original` | 部分专利只收录**英文名** | 中文名以检索结果里的 `assignee`（原文名）为准，或另行核对官方著录 |
| `abstract` / `title`（`--lang zh` 且对象是外文专利） | Google 的**机器翻译** | 必须标注是机译 |
