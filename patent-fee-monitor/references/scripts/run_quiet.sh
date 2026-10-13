#!/bin/bash
# run_quiet.sh - run patent fee collection WITHOUT stealing focus
#
# Usage:
#   ./run_quiet.sh <list.txt> <outdir> [chunk_size]
# Example:
#   ./run_quiet.sh list.txt out 10
#
# What it does:
#   1. Reuse the live session if any (creating a session opens a new Agent Window)
#   2. When a session must be created, pass --no-focus so the window opens in background
#   3. Shrink the Agent Window so it stays out of the way (verified: identical results)
#   4. Restore the original window size (or close the session if we created it) on exit

set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"
export BSK_AUTO_START=0
export BSK_HOME="${BSK_HOME:-$HOME/.bsk}"

# 自动定位解释器与脚本目录，避免写死某台机器的绝对路径。
# 显式覆盖：PY=/path/to/python ./run_quiet.sh ...
# 平台说明：本脚本是 bash。macOS / Linux 直接用；Windows 可在 Git Bash 里跑
#          （bsk 的 install.ps1 会把 %USERPROFILE%\.local\bin 写进 ~/.bashrc），
#          或改为直接调用 collect_batch.py（纯 Python，跨平台）。
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$(command -v python3 || command -v python || true)}"
if [ -z "$PY" ]; then
  echo "[run_quiet] 找不到 python3 / python，请用 PY=/path/to/python 指定" >&2
  exit 9
fi

LIST="${1:?Usage: run_quiet.sh <list.txt> <outdir> [chunk_size]}"
OUTDIR="${2:-out}"
CHUNK="${3:-10}"

QUIET_W=420
QUIET_H=320
NORMAL_W=1280
NORMAL_H=900

sid="$(bsk status --json 2>/dev/null | "$PY" -c 'import sys,json; s=json.load(sys.stdin).get("sessions") or []; print(s[0]["session_id"] if s else "")' 2>/dev/null)"

created=0
if [ -z "$sid" ]; then
  echo "[run_quiet] No live session. Creating one in background (--no-focus)..."
  sid="$(bsk session start --no-focus --json 2>/dev/null | "$PY" -c 'import sys,json; d=json.load(sys.stdin); print(d.get("session_id") or d.get("id") or "")' 2>/dev/null)"
  created=1
  echo "[run_quiet] New session: ${sid:-<failed>}"
else
  echo "[run_quiet] Reusing live session ${sid} (no new window)"
fi

if [ -z "$sid" ]; then
  echo "[run_quiet] ERROR: could not obtain a session id"
  exit 9
fi

# 关键：把会话 id 写进标记文件，让 collect_one.py 的 ensure_session() 复用**同一个**
# 会话。否则两边各建一个 Agent Window，既多弹一次窗，采集还可能落在没登录态的窗口上。
printf '%s' "$sid" > "$HERE/.bsk_quiet_session"

echo "[run_quiet] Shrinking Agent Window to ${QUIET_W}x${QUIET_H}"
bsk window resize --session "$sid" --width "$QUIET_W" --height "$QUIET_H" >/dev/null 2>&1

restore() {
  if [ "$created" = "1" ]; then
    echo "[run_quiet] Done. Stopping the session created by this run."
    bsk session stop "$sid" >/dev/null 2>&1
  else
    echo "[run_quiet] Done. Restoring window to ${NORMAL_W}x${NORMAL_H}"
    bsk window resize --session "$sid" --width "$NORMAL_W" --height "$NORMAL_H" >/dev/null 2>&1
  fi
}
trap restore EXIT

total="$(grep -cve '^\s*$' "$LIST")"
echo "[run_quiet] Total: ${total} entries, chunk size ${CHUNK}"
start=1
while [ "$start" -le "$total" ]; do
  echo "[run_quiet] --- entries ${start}.., chunk ${CHUNK} ---"
  "$PY" "$HERE/collect_batch.py" "$LIST" \
      --outdir "$OUTDIR" --start "$start" --limit "$CHUNK"
  start=$((start + CHUNK))
done

echo "[run_quiet] All done"