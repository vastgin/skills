#!/usr/bin/env python3
"""BrowserSkill（bsk）公共封装 —— 供 专利检索报告 技能的各脚本复用。

职责：
  1. 定位 bsk 可执行文件与 BSK_HOME（优先复用**已在运行**的 daemon，避免多起窗口）
  2. 保证有一个可用的 session
  3. 保证有一个 patents.google.com 标签页
  4. 提供 navigate / evaluate 两个原语（evaluate 直接拿结构化 JSON 返回值）

设计要点（踩过的坑，勿改）：
  - `bsk` 不在默认 PATH，通常在 ~/.local/bin/bsk
  - 每条 bsk 命令都必须显式带 BSK_HOME（沙箱 shell 之间环境不保留）
  - `bsk session start` 会新开一个 Chrome「Agent Window」→ 反复 start 就是反复弹窗。
    因此**优先复用存活 session**，只有确实没有时才 start。
  - `bsk evaluate` 的表达式是**位置参数**（不是 --expression），且必须用 argv 列表传参
    （绝不过 shell），否则长 JS 里的引号会被吞。
  - 一律加 `--json`，返回形如 {"ok":true,"tab_id":N,"value":"..."}，解析稳定。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SEED_URL = "https://patents.google.com/"
PATENTS_HOST = "patents.google.com"


class BskError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 定位 bsk / BSK_HOME
# --------------------------------------------------------------------------- #
def find_bsk() -> str:
    """返回 bsk 可执行文件路径。"""
    cand = Path.home() / ".local" / "bin" / "bsk"
    if cand.exists() and os.access(cand, os.X_OK):
        return str(cand)
    found = shutil.which("bsk")
    if found:
        return found
    raise BskError(
        "找不到 bsk 可执行文件。请先安装 BrowserSkill："
        "https://chromewebstore.google.com/ 搜索 BrowserSkill 扩展，并安装 bsk CLI（见 references/browser-skill-setup.md）"
    )


def _home_from_lsof() -> str | None:
    """从运行中的 daemon 的 UDS 路径反推 BSK_HOME。"""
    lsof = "/usr/sbin/lsof" if os.path.exists("/usr/sbin/lsof") else "lsof"
    try:
        out = subprocess.run([lsof, "-U"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    for line in out.splitlines():
        low = line.lower()
        if "bsk" in low and "daemon.sock" in low:
            p = line.split()[-1]
            if p.endswith("/run/daemon.sock"):
                return p[: -len("/run/daemon.sock")]
    return None


def _candidate_homes() -> list[str]:
    cands: list[str] = []
    env = os.environ.get("BSK_HOME")
    if env:
        cands.append(env)
    lsof_home = _home_from_lsof()
    if lsof_home:
        cands.append(lsof_home)
    for var in ("CODEBUDDY_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        d = os.environ.get(var)
        if d:
            cands.append(os.path.join(d, ".bsk"))
    cands.append(os.path.join(os.getcwd(), ".bsk"))
    # 从 cwd 向上找已存在的 .bsk
    p = Path.cwd()
    for parent in [p, *p.parents]:
        cands.append(str(parent / ".bsk"))
    cands.append(os.path.join(str(Path.home()), ".bsk"))
    seen, out = set(), []
    for c in cands:
        c = os.path.abspath(os.path.expanduser(c))
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def discover_bsk_home() -> str:
    """确定 BSK_HOME。

    关键：**优先复用已经在跑的 daemon**（其 home 里有 run/daemon.sock）。
    另起一个 home 会再开一个浏览器窗口，用户很反感；指向别的 home 也可能没权限建 daemon.json。
    只有在完全找不到现存 home 时，才落到「当前工作区 .bsk」这个默认位置。
    """
    cands = _candidate_homes()
    for c in cands:
        if os.path.exists(os.path.join(c, "run", "daemon.sock")):
            return c
    return cands[0]


# --------------------------------------------------------------------------- #
# 底层调用
# --------------------------------------------------------------------------- #
class Bsk:
    def __init__(self, bsk_home: str | None = None, auto_start: bool = False, verbose: bool = False):
        self.bin = find_bsk()
        self.home = bsk_home or discover_bsk_home()
        self.auto_start = auto_start
        self.verbose = verbose

    # -- 基础 ----------------------------------------------------------------
    def _env(self) -> dict:
        env = dict(os.environ)
        env["BSK_HOME"] = self.home
        # 由本模块显式管理会话生命周期，禁止 CLI 隐式拉起 daemon
        env["BSK_AUTO_START"] = "1" if self.auto_start else "0"
        return env

    def raw(self, *args: str, timeout: int = 120, check: bool = True) -> str:
        cmd = [self.bin, *args]
        if self.verbose:
            print("  $ " + " ".join(cmd[:6]) + (" ..." if len(cmd) > 6 else ""), file=sys.stderr)
        p = subprocess.run(cmd, capture_output=True, text=True, env=self._env(), timeout=timeout)
        if check and p.returncode != 0:
            raise BskError(
                f"bsk {' '.join(args[:3])} 失败（exit {p.returncode}）\n"
                f"STDOUT: {p.stdout.strip()[:500]}\nSTDERR: {p.stderr.strip()[:500]}"
            )
        return p.stdout or p.stderr

    def raw_json(self, *args: str, timeout: int = 120):
        out = self.raw(*args, timeout=timeout)
        return _extract_json(out)

    def raw_text(self, *args: str, timeout: int = 120, check: bool = True) -> str:
        """不带 --json 的普通输出（已去掉 bsk 的提示行）。"""
        out = self.raw(*args, timeout=timeout, check=check)
        lines = [l for l in out.splitlines() if not l.startswith("!")]
        return "\n".join(lines).strip()

    # -- 环境检查 ------------------------------------------------------------
    def doctor(self) -> dict:
        # bsk doctor 在存在问题时返回非 0，这里要拿到输出而不是抛错
        txt = self.raw_text("doctor", check=False)
        state = {}
        for line in txt.splitlines():
            # 形如：  ok    daemon running                   pid 75114 at ws://...
            m = re.match(r"^\s*(\S+)\s+(.+?)\s{2,}\S", line)
            if m:
                state[m.group(2).strip()] = m.group(1).strip().lower()
        return {"raw": txt, "state": state}

    def status_of(self, name: str) -> str | None:
        for line in self.doctor()["raw"].splitlines():
            if name in line:
                return line.split()[0].strip().lower()
        return None

    def ensure_ready(self) -> None:
        info = self.doctor()
        s = info["state"]
        problems = []
        if s.get("extension connected") != "ok":
            problems.append("浏览器扩展未连接（请在 Chrome 中启用 BrowserSkill 扩展并保持浏览器打开）")
        if s.get("daemon running") != "ok":
            problems.append(f"bsk daemon 未运行（BSK_HOME={self.home}）")
        if problems:
            raise BskError(
                "BrowserSkill 环境未就绪：\n  - "
                + "\n  - ".join(problems)
                + f"\n\n排查：`bsk doctor`（BSK_HOME={self.home}）；安装引导见 references/browser-skill-setup.md"
            )

    # -- 会话 ----------------------------------------------------------------
    def sessions(self) -> list[dict]:
        try:
            data = self.raw_json("session", "list", "--json")
        except BskError:
            return []
        if isinstance(data, dict):
            data = data.get("sessions", [])
        return data or []

    def ensure_session(self) -> str:
        """返回一个可用 session id；能复用就绝不起新窗口。"""
        for s in self.sessions():
            # 只认有 Agent Window 的可用会话
            sid = s.get("session_id") or s.get("id") or s.get("session")
            if sid:
                return str(sid)
        out = self.raw_text("session", "start", "--no-focus")
        sid = ""
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line and " " not in line and not line.startswith(("!", "session")):
                sid = line
                break
        if not sid:
            # 兜底：再列一次
            for s in self.sessions():
                sid = str(s.get("session_id") or s.get("id") or s.get("session") or "")
                if sid:
                    break
        if not sid:
            raise BskError(f"无法创建 BrowserSkill 会话，输出：{out[:300]}")
        return sid

    # -- 标签页 --------------------------------------------------------------
    def tabs(self, session: str, scope: str = "agent") -> list[dict]:
        """只列 Agent Window 里的标签页（scope=agent）。

        **绝不使用 `--scope all`**：那会带上用户自己的标签页，一旦误导航就是
        把用户正在看的页面顶掉。所有操作只在自己的 Agent Window 内进行。
        """
        try:
            data = self.raw_json("tab", "list", "--session", session, "--scope", scope, "--json")
        except BskError:
            return []
        if isinstance(data, dict):
            data = data.get("tabs", [])
        return data or []

    def find_patents_tab(self, session: str) -> int | None:
        for t in self.tabs(session):
            url = t.get("url") or ""
            if PATENTS_HOST in url:
                tid = t.get("tab_id") or t.get("id")
                if tid is not None:
                    return int(tid)
        return None

    def ensure_patents_tab(self, session: str, url: str = DEFAULT_SEED_URL) -> int:
        tid = self.find_patents_tab(session)
        if tid is not None:
            return tid
        return self.navigate(session, url)

    # -- 原语 ----------------------------------------------------------------
    def navigate(self, session: str, url: str, tab_id: int | None = None, wait_until: str = "load") -> int:
        args = ["navigate", "--session", session]
        if tab_id is not None:
            args += ["--tab-id", str(tab_id)]
        args += ["--wait-until", wait_until, url]
        out = self.raw_text(*args, timeout=90)
        for line in out.splitlines():
            if line.startswith("tab="):
                return int(line.split("=", 1)[1].split()[0])
        # 兜底
        tid = self.find_patents_tab(session)
        if tid is None:
            raise BskError(f"navigate 未返回标签页 id，输出：{out[:300]}")
        return tid

    def evaluate(self, session: str, tab_id: int, expression: str, timeout: int = 60):
        """执行 JS 表达式并返回解析后的值。

        表达式若返回 JSON 字符串，这里会自动二次解析成 Python 对象。
        """
        data = self.raw_json(
            "evaluate",
            "--session", session,
            "--tab-id", str(tab_id),
            "--json",
            "--timeout", f"{timeout}s",
            expression,
            timeout=timeout + 30,
        )
        if not isinstance(data, dict) or "value" not in data:
            raise BskError(f"evaluate 返回异常：{str(data)[:300]}")
        val = data["value"]
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (ValueError, TypeError):
                return val
        return val

    def evaluate_file(self, session: str, tab_id: int, js_path: str, replacements: dict | None = None, timeout: int = 60):
        code = Path(js_path).read_text(encoding="utf-8")
        for k, v in (replacements or {}).items():
            code = code.replace(k, json.dumps(v, ensure_ascii=False))
        return self.evaluate(session, tab_id, code, timeout=timeout)

    def wait_ready(self, session: str, tab_id: int, timeout_s: float = 20.0) -> bool:
        """轮询页面 document.readyState。"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                st = self.evaluate(session, tab_id, "document.readyState", timeout=15)
                if st in ("interactive", "complete"):
                    return True
            except BskError:
                pass
            time.sleep(0.3)
        return False


# --------------------------------------------------------------------------- #
def _extract_json(text: str):
    """从 bsk 输出里抠出 JSON（容忍前置提示行）。"""
    text = text.strip()
    if not text:
        raise BskError("bsk 无输出")
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except ValueError:
                continue
    raise BskError(f"无法解析 bsk 输出为 JSON：{text[:300]}")


def connect(verbose: bool = False, check: bool = True) -> Bsk:
    b = Bsk(verbose=verbose)
    if check:
        b.ensure_ready()
    return b


if __name__ == "__main__":
    b = Bsk()
    print("bsk     :", b.bin)
    print("BSK_HOME:", b.home)
    print("doctor  :")
    print(b.doctor()["raw"])
