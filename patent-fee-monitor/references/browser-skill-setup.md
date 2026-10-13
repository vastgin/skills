# BrowserSkill 依赖与安装引导

本技能**所有浏览器操作必须经 BrowserSkill**（`browser-skill` / `bsk` 命令）完成：驱动用户真实登录的 Chromium，走 DOM 读页面。

**禁止绕过它**——不直连 cpquery 内部接口、不用 curl/requests 伪造请求、不换其它浏览器后端。既是站点使用边界问题，也拿不到登录态。

## 1. 可用性检查（每次任务开始前做一次）

```sh
export PATH="$HOME/.local/bin:$PATH"   # bsk 默认装在 ~/.local/bin，不在默认 PATH
export BSK_AUTO_START=0
export BSK_HOME=<真实 daemon home>      # 见下
bsk --version                           # 有版本输出 = CLI 在位
bsk doctor                              # 关键：看末尾几行
```

`BSK_HOME` 不要凭猜：`lsof -U | grep bsk` 找出**真正在跑的** daemon 及其 socket 路径（形如 `<工作目录>/.bsk/run/daemon.sock`），复用它的 home。
（Windows 没有 `lsof`：daemon home 默认就是 `%USERPROFILE%\.bsk`，用 `Get-ChildItem $HOME\.bsk` 确认；只要 `bsk status` 能正常输出，通常无需显式设置 `BSK_HOME`。）

`bsk doctor` 结果分流：

| 末行状态 | 含义 | 下一步 |
|---|---|---|
| `ok extension connected  N browser(s) connected` + `ok browser protocol compatible` | 就绪 | 继续 SKILL.md › Step 0 |
| 缺 `daemon running` | 守护进程没起 | 按 `browser-skill` 技能说明起 daemon（host 持久后台任务里 `bsk daemon start --foreground`），**不要**删运行时文件或另起一个 |
| `extension connected` 缺失或 0 browsers | 扩展未连上 | 走 §2 第二步 |
| `protocol` 不兼容 | CLI/daemon/扩展版本不一致 | `bsk update` 后重跑 doctor |

agent 侧技能是否就位：`bsk install-skill --list | grep -i workbuddy` → 显示 `skill installed` 即可。

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

各项为 `ok` 后回到 `SKILL.md` › Step 0 继续。

## 3. 引导话术（可直接用）

> 这个任务要在**您本人已登录的浏览器**里操作国知局查询平台，用到一种叫 BrowserSkill 的浏览器自动化能力（只在您本机运行，不上传您的登录凭证）。检测到本机还没装好，麻烦您花两分钟装一下：
>
> 1. 打开终端执行：`<上面对应系统的安装命令>`
> 2. 在浏览器里安装扩展：`<Chrome 商店链接>`（装完点扩展图标，确认显示「已连接」）
> 3. 再执行：`bsk install-skill --all`
>
> 都完成后告诉我一声，我来继续。若您不想装，也可以改由您本人在网站上人工查询，我提供号码清单和应查字段清单。

## 4. 常见故障

| 现象 | 处置 |
|---|---|
| `bsk: command not found` | PATH 未含 `~/.local/bin`；先 `export` 再试，仍无则走第一步安装 |
| `bind UDS ... Address already in use` | UDS 路径存在即 bind 失败，**这不能证明有守护进程在跑**（多为残留 socket 文件）。用 `lsof -U \| grep bsk`（Windows：`Get-Process bsk`）找真正运行的 daemon 及其 `BSK_HOME` 并**复用之**；不要删运行时文件，也不要另起一个守护进程 |
| `browsers` 列表为空 | 扩展没连上：确认扩展已启用、浏览器与 daemon 在同一台机器 |
| 权限被拒 / 沙箱拦截 | 命令默认留在沙箱内跑；确需越权的场景先向用户说明并取得同意，不要隐蔽绕过 |
| `session not registered` | 之前会话已停（休眠/超时）：`bsk session start --json` 重开，旧 tab-id 全部作废，**不要复用旧 id** |
| doctor 报版本不兼容 | `bsk update` 后重跑 doctor；三者（CLI / daemon / 扩展）需同代 |
