#!/usr/bin/env python3
"""Google Patents 详情取数 —— 进详情页拿完整著录项（标题/申请人/发明人/日期/
分类号/同族/引用数/法律状态/法律事件/摘要...[可选]权利要求与说明书）。

流程：逐个公开号 `navigate` 到 `https://patents.google.com/patent/<PUB>/<lang>`
→ 在该页上下文里 `fetch(location.href)` 取回**服务端渲染 HTML** → 按语义标记抽字段。

为什么不直接读渲染后的 DOM：Google Patents 是 Polymer 应用，水合后会把服务端渲染的
语义标记整体替换掉（实测 `[itemprop]` 全空），字段反而变少。详见 SKILL.md。

示例：
  detail.py CN110000000A
  detail.py CN110000000A CN110000000B --lang zh --with-claims
  detail.py --pub-file pubs.txt --lang zh --out /tmp/details.json
  detail.py CN110000000A --lang zh --with-claims --with-citations --format json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bsk_common import Bsk, BskError  # noqa: E402
from gp_util import emit, is_application_number, log, normalize_pub  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL_JS = os.path.join(HERE, "js", "detail.js")
SEARCH_JS = os.path.join(HERE, "js", "search.js")

SUMMARY_COLS = ["publication_number", "publication_date", "grant_date", "assignee_current", "title"]


def appno_candidates(raw: str) -> list[str]:
    """申请号 → 可能的 Google 检索词。

    实测规则：CN 申请号里的**校验位会让检索命中 0 条**，必须去掉。
      - 现行 12 位 + 「.校验位」：202210114753.X → 202210114753
      - 旧式 13 位（末位是校验位）：2018111401422 → 201811140142
    """
    digits = re.sub(r"[^0-9Xx]", "", raw)
    out = []
    if len(digits) == 13:
        out.append(digits[:12])
    if len(digits) == 12:
        out.append("CN" + digits)
    out.append(digits)
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def resolve_appno(bsk, session, tab, raw: str):
    """用检索接口把申请号换成公开号。返回 (publication_number, 检索词, 命中数, tab)。"""
    last = (None, None, 0, tab)
    for cand in appno_candidates(raw):
        try:
            r = bsk.evaluate_file(session, tab, SEARCH_JS, {"__GP_QUERY__": {"q": cand, "num": 10, "page": 0}})
        except BskError:
            continue
        if not r or not r.get("ok"):
            continue
        hits = [h for h in (r.get("results") or []) if h.get("publication_number")]
        if hits:
            return hits[0]["publication_number"], cand, r.get("total") or len(hits), tab
        last = (None, cand, 0, tab)
    return last


def read_pubs(args) -> list[str]:
    pubs: list[str] = list(args.pubs or [])
    if args.pub_file:
        with open(args.pub_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 兼容 "CN110000000A,备注" / "CN110000000A\t备注" 这类带注释的行
                pubs.append(line.replace(",", "\t").replace(";", "\t").split("\t")[0].strip())
    out, seen = [], set()
    for p in pubs:
        n = normalize_pub(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Google Patents 详情取数")
    ap.add_argument("pubs", nargs="*", help="公开号（可多个），支持 CN110000000A / CN 110000000 A / 详情页 URL")
    ap.add_argument("--pub-file", default="", help="从文件按行读公开号（# 开头为注释）")
    ap.add_argument("--lang", default="zh", help="详情页语言：zh（原文，中文专利给中文标题）/ en（英文）。默认 zh")
    ap.add_argument("--with-abstract", dest="with_abstract", action="store_true", default=True)
    ap.add_argument("--without-abstract", dest="with_abstract", action="store_false")
    ap.add_argument("--with-claims", action="store_true", help="带权利要求全文")
    ap.add_argument("--with-description", action="store_true", help="带说明书全文（很大）")
    ap.add_argument("--with-citations", action="store_true", help="带引用文献明细（backward）")
    ap.add_argument("--with-cited-by", action="store_true", help="带被引明细（forward）")
    ap.add_argument("--with-similar", action="store_true", help="带相似文献明细")
    ap.add_argument("--no-navigate", action="store_true", help="不逐条打开详情页，只做同源取数（更快，但浏览器里看不到翻页过程）")
    ap.add_argument("--no-resolve-appno", dest="resolve_appno", action="store_false", default=True,
                    help="禁用「申请号自动换公开号」（默认开启：识别到申请号时先检索换号）")
    ap.add_argument("--delay", type=float, default=0.3, help="每条之间的间隔秒数，默认 0.3")
    ap.add_argument("--retry", type=int, default=2, help="单条失败重试次数，默认 2")
    ap.add_argument("--format", default="json", choices=["json", "ndjson", "csv", "tsv", "table"])
    ap.add_argument("--out", default="", help="结果写文件")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    pubs = read_pubs(args)
    if not pubs:
        ap.error("请给出至少一个公开号（位置参数或 --pub-file）")

    opt = {
        "source": "server-html",
        "with_abstract": args.with_abstract,
        "with_claims": args.with_claims,
        "with_description": args.with_description,
        "with_citations": args.with_citations,
        "with_cited_by": args.with_cited_by,
        "with_similar": args.with_similar,
    }

    try:
        bsk = Bsk(verbose=args.verbose)
        bsk.ensure_ready()
        session = bsk.ensure_session()
        tab = bsk.ensure_patents_tab(session)
    except BskError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    items, errors = [], []
    n = len(pubs)
    for i, pub in enumerate(pubs, 1):
        resolved = None
        if is_application_number(pub):
            if not args.resolve_appno:
                msg = f"{pub} 是申请号，不是公开号（已按 --no-resolve-appno 跳过）"
                log(f"[{i}/{n}] {pub} 跳过：{msg}")
                errors.append({"publication_number": pub, "error": msg})
                continue
            resolved, q, total, tab = resolve_appno(bsk, session, tab, pub)
            if not resolved:
                msg = (f"{pub} 是申请号，且自动换号失败（试过：{', '.join(appno_candidates(pub))}）。"
                       f'可手动检索：search.py --raw "q={appno_candidates(pub)[0]}"')
                log(f"[{i}/{n}] {pub} 失败：{msg}")
                errors.append({"publication_number": pub, "error": msg})
                continue
            if total and total > 5:
                log(f"[{i}/{n}] {pub} 申请号命中 {total} 条，取第一条（可能不唯一，请人工复核）")
            log(f"[{i}/{n}] {pub} 申请号 → {resolved}（检索词 {q}）")
            pub = resolved

        url = f"https://patents.google.com/patent/{pub}/{args.lang}"
        last_err = None
        for attempt in range(args.retry + 1):
            t0 = time.time()
            try:
                if not args.no_navigate:
                    tab = bsk.navigate(session, url, tab_id=tab, wait_until="domcontentloaded")
                r = bsk.evaluate_file(session, tab, DETAIL_JS, {"__GP_OPT__": dict(opt, url=url)}, timeout=90)
                if r and r.get("ok"):
                    d = r["data"]
                    html_len = (d.get("_meta") or {}).get("html_bytes") or 0
                    if html_len < 3000:
                        raise BskError(f"详情页异常（服务端 HTML 仅 {html_len} 字节，可能是无效公开号或反爬拦截）")
                    d["_meta"]["elapsed_s"] = round(time.time() - t0, 2)
                    if resolved:
                        d["_meta"]["resolved_from_appno"] = pubs[i - 1]
                    items.append(d)
                    log(f"[{i}/{n}] {pub} ok · {time.time() - t0:.1f}s · {(d.get('title') or '')[:40]}")
                    break
                last_err = (r or {}).get("error") or "未知错误"
            except BskError as e:
                last_err = str(e)
            if attempt < args.retry:
                time.sleep(1.0 + attempt)
        else:
            log(f"[{i}/{n}] {pub} 失败：{last_err}")
            errors.append({"publication_number": pub, "url": url, "error": last_err})

        if i < n and args.delay:
            time.sleep(args.delay)

    payload = {
        "ok": bool(items),
        "source": "patents.google.com",
        "lang": args.lang,
        "requested": n,
        "succeeded": len(items),
        "failed": len(errors),
        "errors": errors,
        "results": items,
    }

    if args.out:
        emit(payload, args.format, "results", args.out)
    else:
        emit(payload, args.format, "results")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
