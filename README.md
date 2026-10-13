# vastgin skills

vastgin 即**维思勤**（官网 [www.vastgin.com](https://www.vastgin.com)）。本仓库是 vastgin 维护的 Agent Skill 集合，供 CodeBuddy / WorkBuddy 等 Agent 客户端按需加载，把需要人参与、步骤繁复的线上作业固化为可复用、可审计的工作流。

每个子目录 = 一个技能（含 `SKILL.md` 入口、`references/` 参考资料、`examples/` 契约样例）。

## 仓库结构

```
patent-fee-monitor/          # 专利年费监控
├── SKILL.md                 # 技能入口：步骤索引 + 红线
├── examples/
│   └── sample-output.json   # 上报契约全集示例
└── references/
    ├── browser-skill-setup.md   # BrowserSkill 依赖检查与安装引导
    ├── login-guide.md           # CNIPA 登录引导与掉线恢复
    ├── query-playbook.md        # 采集剧本：导航、判定树、字段翻译
    ├── data-schema.yaml         # SaaS 上报契约（权威机器可读版）
    ├── token-setup.md           # Token 获取与轮换
    ├── troubleshooting.md       # 故障分级处置
    └── scripts/                 # 可执行实现
        ├── README.md
        ├── collect_one.py       # 单条端到端采集
        ├── collect_batch.py     # 批量采集（逐条打印进度）
        ├── push_mcp.py          # 直连 MCP 端点上报
        ├── make_report.py       # 对账报告生成
        └── run_quiet.sh         # 一键静默采集
```

## 技能一览

| 技能 | 说明 | 入口 |
|---|---|---|
| `patent-fee-monitor` | 从国家知识产权局（CNIPA）案件查询平台采集已授权专利的年费数据（著录、授权公告日、未缴年费前 2 条、已缴年费前 2 条、滞纳金阶梯），经 MCP 推送到 SaaS 年费监控产品 | [`patent-fee-monitor/SKILL.md`](patent-fee-monitor/SKILL.md) |

## 安装使用

把本仓库克隆到本地后，将技能目录放到 Agent 的技能搜索路径下（或直接在客户端中指向 `<技能名>/SKILL.md`）：

```sh
git clone git@github.com:vastgin/skills.git
```

以 `patent-fee-monitor` 为例，运行前需要：

1. **BrowserSkill（硬性依赖）**：所有页面操作必须经 `browser-skill`（`bsk`）驱动用户本人已登录的浏览器，不直连站点内部接口。检查与安装见
   [`references/browser-skill-setup.md`](patent-fee-monitor/references/browser-skill-setup.md)。
2. **SaaS API Token**：从 SaaS「个人设置」获取，配置到 MCP 的 `Authorization` 头或写入 gitignore 的 `.env`，
   详见 [`references/token-setup.md`](patent-fee-monitor/references/token-setup.md)。**禁止把 token 粘贴进对话**。

基本流程：

```sh
export PATH="$HOME/.local/bin:$PATH"     # bsk 装在这里
export BSK_AUTO_START=0
export BSK_HOME=<daemon home>            # 用 lsof -U | grep bsk 确认，不要猜

cd patent-fee-monitor
python3 references/scripts/collect_one.py 2018111401422 --outdir out   # 单条采集
python3 references/scripts/collect_batch.py list.txt --outdir out      # 批量（默认 10 条/批）
python3 references/scripts/push_mcp.py --dir out --dry-run            # 上报（先 dry-run）
```

脚本跨平台（纯 Python + 页面 JS），Windows 用 `install.ps1` 装 `bsk.exe` 并在 Git Bash 或 PowerShell 中运行。

## 设计红线

所有技能共同遵守：

- **禁止编造数据**：页面上没有的字段就是没有，缺什么如实报什么。
- **凭证只存在于 `Authorization` 头**：不写入 git 跟踪的文件、日志、payload 或对话正文。
- **浏览器操作只经 BrowserSkill**：不绕过它直连接口、不伪造请求、不换其它浏览器后端；依赖未就绪时先引导安装，不谎称已完成。
- **长任务必须分批播报进度**：禁止静默批量执行，失败条目同样必须播报。
- **不确定时归 `error`**：不误判为终态（如 `not_granted`），保留重试自愈空间。

各技能的完整红线与交付边界见其 `SKILL.md`。
