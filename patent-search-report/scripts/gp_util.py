#!/usr/bin/env python3
"""专利检索报告 的共用小工具：公开号归一、输出格式化、进度打印。"""

from __future__ import annotations

import csv
import io
import json
import re
import sys

LANGS = {"en", "zh", "cn", "de", "fr", "es", "ja", "ko", "ru", "pt", "it", "nl", "sv", "fi", "da", "no", "pl", "tr", "ar", "he", "hu", "cs", "ro", "el", "th", "vi", "id", "uk"}

# 常见公开号形态：CN110000000A / US2023012345A1 / EP1234567B1 / WO2023123456A1
PUB_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]+$")


def normalize_pub(raw: str) -> str:
    """把用户可能给出的各种写法归一成 Google Patents 用的公开号。

    支持：CN110000000A、cn110000000a、CN 110000000 A、CN110000000A/en、
          https://patents.google.com/patent/CN110000000A/en、
          著录里的 CN:110000000:A 形态。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^https?://[^/]+/patent/", "", s, flags=re.I)
    s = re.split(r"[?#]", s)[0]
    s = s.strip().strip("/")

    # CN:110000000:A → CN110000000A
    m = re.match(r"^([A-Za-z]{2}):([A-Za-z0-9/.\-]+?)(?::([A-Za-z]\d?))?$", s)
    if m:
        return (m.group(1) + m.group(2) + (m.group(3) or "")).upper()

    parts = [p for p in s.split("/") if p]
    if len(parts) > 1 and parts[-1].lower() in LANGS:
        parts.pop()
    s = parts[-1] if parts else s
    s = re.sub(r"[\s\u00a0]+", "", s)
    return s.upper()


def looks_like_pub(s: str) -> bool:
    return bool(PUB_RE.match(s or ""))


def is_application_number(s: str) -> bool:
    """申请号（如 201910088018.4 / CN201910088018.4A 里的纯数字部分）不是公开号。"""
    return bool(re.match(r"^\d{8,}[.\d]*[A-Z]?$", (s or "").replace(" ", "")))


def log(msg: str) -> None:
    """进度一律走 stderr，stdout 只留给结构化结果。"""
    print(msg, file=sys.stderr, flush=True)


def flatten(row: dict) -> dict:
    """把嵌套字段压成适合 csv/表格的一层。"""
    out = {}
    for k, v in row.items():
        if isinstance(v, (list, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def to_csv(rows: list[dict], columns: list[str] | None = None, sep: str = ",") -> str:
    if not rows:
        return ""
    cols = columns or list(dict.fromkeys([k for r in rows for k in r.keys()]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", delimiter=sep)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue()


def to_table(rows: list[dict], columns: list[str], max_width: int = 48) -> str:
    if not rows:
        return "(无结果)"
    widths = {}
    for c in columns:
        w = len(c)
        for r in rows:
            w = max(w, min(max_width, len(str(r.get(c) if r.get(c) is not None else ""))))
        widths[c] = min(w, max_width)
    line = "  ".join(c.ljust(widths[c]) for c in columns)
    out = [line, "  ".join("-" * widths[c] for c in columns)]
    for r in rows:
        cells = []
        for c in columns:
            v = str(r.get(c) if r.get(c) is not None else "").replace("\n", " ")
            if len(v) > widths[c]:
                v = v[: widths[c] - 1] + "…"
            cells.append(v.ljust(widths[c]))
        out.append("  ".join(cells))
    return "\n".join(out)


def summary_columns(rows: list[dict], columns: list[str] | None = None) -> list[str]:
    """摘要表格用哪几列：优先显式指定，否则挑几个通用字段兜底。"""
    if columns:
        return columns
    if not rows:
        return []
    prefs = ["publication_number", "publication_date", "grant_date", "assignee_current", "assignee", "title"]
    cols = [c for c in prefs if c in rows[0]]
    return cols or list(rows[0].keys())[:6]


def emit(payload: dict, fmt: str = "json", rows_key: str = "results", out: str | None = None,
         columns: list[str] | None = None, summary_rows: int = 10) -> None:
    """统一输出。

    - 不给 --out：stdout 输出完整结果（json / ndjson / csv / tsv / table）。
    - 给了 --out：完整结果落盘，**stdout 只给一份精简摘要**（元信息 + 前若干行表格），
      避免几十上百条明细把对话上下文冲爆。要看全量就打开落盘文件。
    """
    rows = payload.get(rows_key) or []
    if fmt == "ndjson":
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    elif fmt in ("csv", "tsv"):
        text = to_csv([flatten(r) for r in rows], columns, sep="," if fmt == "csv" else "\t")
    elif fmt == "table":
        text = to_table(rows, summary_columns(rows, columns))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)

    if not out:
        print(text)
        return

    if fmt in ("csv", "tsv"):
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(text)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    log(f"已写出：{out}")

    meta = {
        k: v
        for k, v in payload.items()
        if k not in (rows_key, "errors", "hits") and not isinstance(v, (list, dict))
    }
    if payload.get("errors"):
        meta["errors"] = payload["errors"][:3]
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if rows:
        print(to_table(rows[:summary_rows], summary_columns(rows, columns)))
        if len(rows) > summary_rows:
            print(f"（共 {len(rows)} 条，完整结果见 {out}）")
        else:
            print(f"（共 {len(rows)} 条，完整结果见 {out}）")


def write_json(path: str | None, payload: dict) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"已写出：{path}")
