# 故障排查

## 0. 最先做的三件事

```sh
export PATH="$HOME/.local/bin:$PATH"
bsk doctor                     # 看 daemon / 扩展 / 协议
# 若 doctor 里 BSK_HOME 不对：lsof -U | grep bsk 找真正在跑的 daemon 复用（脚本会自动找）
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://patents.google.com/   # 浏览器能开的就能开
```

本技能的脚本会自己探测 `BSK_HOME` 与会话，一般不用手动 export；但排查时手动跑一遍最快。

## 1. 环境类

| 现象 | 原因 | 处置 |
|---|---|---|
| `BrowserSkill 环境未就绪：bsk daemon 未运行` | daemon 没起，或起了但 home 不是同一个 | `lsof -U \| grep bsk` 看真正在跑的 daemon 及其 `run/daemon.sock` 路径，**复用它的 home**；要重起就在**持久后台任务**里跑 `bsk daemon start --foreground` |
| `浏览器扩展未连接` | 扩展没启用 / 浏览器没开 | 打开 Chrome，确认 BrowserSkill 扩展已启用；`bsk doctor` 应显示 `1 browser(s) connected` |
| `bind UDS ... Address already in use` | 残留的 socket 文件，**不代表有 daemon 在跑** | 不要删运行时文件、不要另起 daemon；先 `lsof -U \| grep bsk` 确认真相 |
| `session not registered` / 会话丢了 | daemon 重启或会话超时 | 脚本会自动 `session start`；旧的 tab-id 作废，不要复用 |
| 浏览器被反复弹窗 | 每次都 `session start` 会新开 Agent Window | 脚本已优先复用存活会话；daemon 常驻是会话存活的前提。**别手动杀 daemon** |
| 采集完想立刻清场 | 默认保留会话（下次复用不弹窗） | `bsk session stop --all` |

## 2. 检索类

| 现象 | 原因 | 处置 |
|---|---|---|
| 命中 0 条 | ① 词太窄 ② 日期格式不对 ③ 传了带校验位的申请号 ④ 该库没收录 | ① 换同义词/放宽 ② 日期**必须 8 位 `YYYYMMDD`**（`priority:2024` 会得 0 条）③ 申请号去掉校验位（`2018111401422` → `201811140142`）④ 改用公开号直查确认是否存在 |
| `total` 大得离谱且多个不同词数字一样 | Google 的 `total_num_results` 对宽泛词**饱和**在 ~12.5 万 | 当量级看，别当精确数；要精确就先收窄条件（见 `query-syntax.md` §5） |
| 翻页翻不动 / `total_num_pages` 恒为 100 | Google 上限：最多第 100 页 | 用更细的条件（分类号、日期窗、申请人）切分集合 |
| `num<10` 不生效 | 服务端小于 10 按 10 处理 | 预期行为，脚本已把页大小夹在 10–100 |
| `--assignee` 中文名查不到 | 名称写法与 Google 收录不一致 | 去掉「有限公司」等后缀试，或用检索式 `q=公司名` |

## 3. 详情类

| 现象 | 原因 | 处置 |
|---|---|---|
| `HTTP 404 for https://patents.google.com/patent/XXX/zh` | 公开号不存在，或种类代码不对（A 申请公布 / B 授权公告 / U 实用新型 / S 外观设计） | 换种类代码试；或用 `search.py --raw "q=<申请号去校验位>"` 先查真实公开号 |
| `详情页异常（服务端 HTML 仅 N 字节…）` | 无效公开号，或触发了反爬/被网络拦截 | 先确认浏览器里手工打开该 URL 是否正常；连续大量请求会触发限流，**放慢 `--delay`** |
| 详情字段大面积为空（只有 `pdf_url`/`url` 有值） | 页面结构变了 | 这是**服务端 HTML 的语义标记**变了，改 `scripts/js/detail.js`；先跑一次 `--with-abstract` 看 `_meta.html_bytes` 是否正常（正常 30 万–100 万字节量级） |
| 摘要/标题是外文机翻 | 用了 `--lang zh` 而对象是外文专利（Google 提供机译） | 需要原文就换 `--lang en`；交付时要**标注是机器翻译** |
| 申请人是英文名 | Google 对部分专利只收录英文名 | 中文名去检索结果的 `assignee`（`search.py` 返回的是原文名）或官方渠道取 |
| `grant_date` 与官方不一致 | 它是**推导值**（GR01 事件日期，或同族 B 文本公开日） | 需要精确授权公告日时，以专利局公布的官方文本为准（见 `field-map.md`） |

## 4. 网络与策略

- **Google 域名要能出去。** Google Patents 在中国大陆需要可用的网络出口。脚本走的是**用户浏览器**，所以**浏览器能打开 patents.google.com，脚本就能跑**；在沙箱里用 `curl` 直连会被墙（那是预期现象，不代表技能有问题）。
- **不要并发。** 检索/详情都是打 Google 的接口，并发容易触发限流。脚本串行执行，`--delay` 默认 0.3s，量大时调到 1s。
- **不要用 Python 直接发 HTTP 去取数**（也不要用 curl 绕过）。一是不符合本技能的边界，二是拿不到浏览器的出口与真实会话。同理**不要换别的浏览器后端**。
