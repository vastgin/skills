# Token 获取与轮换（Step 0 之前）

用户从 SaaS 平台获取个人 api_token 后，按以下方式之一提供；**禁止要求用户把 token 直接粘贴进对话**。

## 方式 1：WorkBuddy MCP 配置（推荐）

用户在 WorkBuddy 的 MCP 服务器配置中，为 `http://localhost:48080/open-api/mcp` 设置请求头：

```
Authorization: Bearer <token>
```

token 活在配置层，agent 只调 `check_token` / `push_fee_batch` 工具，**永不接触 token 值**。一次配置长期有效。

## 方式 2：本地测试 `.env`

用户将 `IPBUDDY_SAAS_TOKEN=<token>` 写入项目根 `.env`（已 gitignore）。agent 侧约定：

- 用 `grep -q IPBUDDY_SAAS_TOKEN .env` **确认存在但不打印值**；
- curl 调用时以 `$(grep ...)` 内联展开，命令与输出均不回显值；
- 禁止把值写进任何 git 跟踪文件、日志或对话正文。

## 方式 3：用户坚持粘贴到对话

接受本次使用，但**用后必须提醒**去 SaaS 端轮换该 token。

## 引导话术

> 请先在 SaaS 平台「个人设置」里获取您的 API Token，然后：①在 WorkBuddy 的 MCP 设置里把它填到 open-api 服务器的 Authorization 头（推荐，一次配置长期有效）；或 ②写入本项目根目录 `.env` 文件的 `IPBUDDY_SAAS_TOKEN=` 行。两种方式我都拿不到您的 token 明文，更安全。

## 轮换

- token 失效（`check_token` invalid）→ 引导用户去 SaaS 重新下发，**不开始采集**；
- 曾以方式 3 使用过的 token → 在对账汇总末尾追加轮换提醒。
