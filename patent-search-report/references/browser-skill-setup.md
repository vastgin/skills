# BrowserSkill 依赖与安装引导

本技能**所有页面访问必须经 BrowserSkill**（技能名 `browser-skill`，命令 `bsk`）在用户真实浏览器里发起。
它是本机运行的浏览器自动化能力（浏览器扩展 + 本地 daemon + CLI）：**不修改站点接口、不上传用户数据**。

> **为什么必须走浏览器而不是直接发 HTTP**：① **复用用户浏览器的网络出口**——Google 域名在很多网络下需要代理，浏览器里已经配好，脚本跟着走即可；② 检索接口是站内**同源**接口，只有在页面上下文里调用才拿得到；③ 不引入第二套 HTTP 栈，也不用把任何凭据搬出浏览器。

## 1. 可用性检查

```sh
export PATH="$HOME/.local/bin:$PATH"   # bsk 默认在 ~/.local/bin，不在默认 PATH
export BSK_AUTO_START=0
bsk --version                          # 有版本输出 = CLI 在位
bsk doctor                             # 关键：看末几行
```

`BSK_HOME` 不用手猜：`lsof -U | grep bsk` 找出**真正在跑的** daemon 及其 socket（形如 `<某目录>/.bsk/run/daemon.sock`），复用它的 home。
本技能的 `scripts/bsk_common.py` 会**自动**按「环境变量 → 运行中 daemon 反推 → 当前工作区 .bsk」的顺序探测，一般无需手动设置。

`bsk doctor` 分流：

| 末行状态 | 含义 | 下一步 |
|---|---|---|
| `ok extension connected  N browser(s) connected` + `ok browser protocol compatible` | 就绪 | 直接跑脚本 |
| 缺 `daemon running` | 守护进程没起 | 在**持久后台任务**里 `bsk daemon start --foreground`；**不要**删运行时文件、也不要另起一个 home |
| `extension connected` 缺失或 0 browsers | 扩展没连上 | 走 §2 第二步 |
| `protocol` 不兼容 | CLI / daemon / 扩展版本不一致 | `bsk update` 后重跑 doctor |

## 2. 引导安装（逐字给用户，**不要**静默替用户装）

按顺序四步，缺哪步补哪步。

### 第一步 · 装 bsk CLI

**macOS / Linux**

```sh
curl -fsSL https://raw.githubusercontent.com/Tencent/BrowserSkill/main/install.sh | sh
export PATH="${BSK_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
```

**Windows（PowerShell）**

```powershell
irm https://raw.githubusercontent.com/Tencent/BrowserSkill/main/install.ps1 | iex
```

### 第二步 · 装浏览器扩展

- Chrome：[Chrome 应用商店](https://chromewebstore.google.com/detail/hhcmgoofomhgciiibhipgmgkgnoenaoi)
- Edge：[Edge 外接程序](https://microsoftedge.microsoft.com/addons/detail/browserskill/emacgiaaaiojkkpkddmmdfhmokgmnikg)
- 其它 Chromium 内核浏览器：装 Chrome 商店版

装完点扩展图标，确认弹窗显示「已连接 / connected」。

### 第三步 · 装 agent 侧技能

```sh
bsk install-skill --all                       # 装到本机所有已检测 harness
# 或只装 WorkBuddy：
bsk install-skill --harness workbuddy -y
```

### 第四步 · 复查

```sh
bsk doctor
```

各项为 `ok` 后即可使用本技能。

## 3. 引导话术（可直接用）

> 这个任务要用一种叫 BrowserSkill 的浏览器自动化能力，在**您自己的浏览器**里打开 Google Patents 检索和取数（只在本机运行，不上传任何隐私数据，也不需要登录帐号）。检测到本机还没装好，麻烦您花两分钟：
>
> 1. 打开终端执行：`<上面对应系统的安装命令>`
> 2. 在浏览器里安装扩展：`<Chrome 商店链接>`（装完点扩展图标，确认显示「已连接」）
> 3. 再执行：`bsk install-skill --all`
>
> 另外请确认您的浏览器能正常打开 <https://patents.google.com/>（该站点在部分网络环境下需要代理）。
>
> 都完成后告诉我一声，我来继续。若您不想装，我也可以把检索式与应查字段清单给您，由您本人在网站上人工检索。

## 4. 常见故障

| 现象 | 处置 |
|---|---|
| `bsk: command not found` | PATH 未含 `~/.local/bin`；先 `export` 再试，仍无则走第一步 |
| `bind UDS ... Address already in use` | UDS 路径存在即 bind 失败，**这不能证明有守护进程在跑**（多为残留 socket）。用 `lsof -U \| grep bsk` 找真正运行的 daemon 并复用；不要删运行时文件、也不要另起一个 |
| daemon 指向别的目录导致 `rename ... Operation not permitted` | `BSK_HOME` 必须落在**当前工作区内**的 `.bsk`；脚本会自动选对，手动调试时注意 |
| 浏览器反复弹窗 | 别反复 `bsk session start`（每次会新开 Agent Window）；优先复用会话，daemon 常驻是前提 |
| `browsers` 列表为空 | 扩展没启用，或浏览器与 daemon 不在同一台机器 |
| 权限被拒 / 沙箱拦截 | 命令默认留在沙箱内跑；确需越权的场景先向用户说明并取得同意，不要隐蔽绕过 |
| `session not registered` | 会话已停：`bsk session start --json` 重开，旧 tab-id 全部作废 |
| 能开网页但检索返回 0 / 超时 | 浏览器的网络出口到不了 Google。请用户确认浏览器里能直接打开 patents.google.com |
