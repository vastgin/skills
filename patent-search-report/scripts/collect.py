#!/usr/bin/env python3
"""一步到位：在 Google Patents 检索 → 取回命中列表 → 逐条进详情页取全量著录项。

适合「帮我查一下 XX 相关的专利，把主要著录项列出来」这类需求。
两步都在同一个浏览器会话里完成，检索式与筛选参数与 search.py 一致。

示例：
  collect.py --query "充电桩 液冷" --country CN --limit 10
  collect.py --assignee "宁德时代新能源科技股份有限公司" --country CN --limit 15 --lang zh --out /tmp/catl.json
  collect.py --query "固态电池" --after priority:20230101 --limit 20 --detail-limit 10 --with-claims
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bsk_common import Bsk, BskError  # noqa: E402
from gp_util import emit, log  # noqa: E402
from search import build_query, outer_query  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_JS = os.path.join(HERE, "js", "search.js")
DETAIL_JS = os.path.join(HERE, "js", "detail.js")
MAX_PER_PAGE = 100


def main() -> int:
    ap = argparse.ArgumentParser(description="Google Patents 检索 + 详情取数（一步到位）")
    ap.add_argument("--query", "-q", default="")
    ap.add_argument("--raw", default="")
    ap.add_argument("--country", default="")
    ap.add_argument("--language", default="", help="文献语言：CHINESE / ENGLISH")
    ap.add_argument("--status", default="", help="GRANT / APPLICATION")
    ap.add_argument("--type", default="")
    ap.add_argument("--assignee", default="")
    ap.add_argument("--inventor", default="")
    ap.add_argument("--before", default="")
    ap.add_argument("--after", default="")
    ap.add_argument("--sort", default="", choices=["", "new", "old"])
    ap.add_argument("--limit", "-n", type=int, default=10, help="检索取多少条（默认 10）")
    ap.add_argument("--detail-limit", type=int, default=0, help="其中前多少条进详情页（默认与 --limit 相同）")
    ap.add_argument("--lang", default="zh", help="详情页语言，默认 zh")
    ap.add_argument("--with-claims", action="store_true")
    ap.add_argument("--with-description", action="store_true")
    ap.add_argument("--with-citations", action="store_true")
    ap.add_argument("--with-cited-by", action="store_true")
    ap.add_argument("--with-similar", action="store_true")
    ap.add_argument("--without-abstract", dest="with_abstract", action="store_false", default=True)
    ap.add_argument("--no-navigate", action="store_true", help="详情不逐条打开页面（更快）")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--retry", type=int, default=2)
    ap.add_argument("--format", default="json", choices=["json", "ndjson", "csv", "tsv", "table"])
    ap.add_argument("--out", default="")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if not (a.query or a.raw or a.assignee or a.inventor):
        ap.error("至少要给 --query / --raw / --assignee / --inventor 之一")
    if a.country:
        a.country = [c.strip().upper() for c in a.country.split(",") if c.strip()]

    detail_limit = a.detail_limit or a.limit
    limit = max(1, a.limit)
    page_size = max(10, min(MAX_PER_PAGE, limit))
    pages_needed = max(1, -(-limit // page_size))

    try:
        bsk = Bsk(verbose=a.verbose)
        bsk.ensure_ready()
        session = bsk.ensure_session()
        tab = bsk.ensure_patents_tab(session)
    except BskError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    # ---------- 第一步：检索 ----------
    search_url = "https://patents.google.com/?" + outer_query(a)
    try:
        tab = bsk.navigate(session, search_url, tab_id=tab, wait_until="domcontentloaded")
    except BskError as e:
        log(f"（提示：打开检索页失败，继续直接取数：{e}）")

    hits: list[dict] = []
    errors: list[dict] = []
    total = None
    seen: set[str] = set()
    for i in range(pages_needed):
        page = i
        q = build_query(a)
        q["num"] = page_size
        q["page"] = page
        try:
            r = bsk.evaluate_file(session, tab, SEARCH_JS, {"__GP_QUERY__": q})
        except BskError as e:
            errors.append({"stage": "search", "page": page, "error": str(e)})
            break
        if not r or not r.get("ok"):
            errors.append({"stage": "search", "page": page, "error": (r or {}).get("error")})
            break
        total = r.get("total")
        batch = r.get("results") or []
        log(f"[检索 第 {page + 1} 页] 命中 {len(batch)} 条 · 累计 {min(len(hits) + len(batch), limit)}/{limit}")
        for row in batch:
            key = row.get("publication_number")
            if not key or key in seen:
                continue
            seen.add(key)
            hits.append(row)
            if len(hits) >= limit:
                break
        if len(hits) >= limit or not batch:
            break
        time.sleep(0.4)

    # ---------- 第二步：详情 ----------
    opt = {
        "source": "server-html",
        "with_abstract": a.with_abstract,
        "with_claims": a.with_claims,
        "with_description": a.with_description,
        "with_citations": a.with_citations,
        "with_cited_by": a.with_cited_by,
        "with_similar": a.with_similar,
    }
    targets = hits[:detail_limit]
    details: list[dict] = []
    for i, hit in enumerate(targets, 1):
        pub = hit["publication_number"]
        url = f"https://patents.google.com/patent/{pub}/{a.lang}"
        last_err = None
        for attempt in range(a.retry + 1):
            t0 = time.time()
            try:
                if not a.no_navigate:
                    tab = bsk.navigate(session, url, tab_id=tab, wait_until="domcontentloaded")
                r = bsk.evaluate_file(session, tab, DETAIL_JS, {"__GP_OPT__": dict(opt, url=url)}, timeout=90)
                if r and r.get("ok"):
                    d = r["data"]
                    d["_search_hit"] = hit
                    details.append(d)
                    log(f"[详情 {i}/{len(targets)}] {pub} ok · {time.time() - t0:.1f}s · {(d.get('title') or '')[:40]}")
                    break
                last_err = (r or {}).get("error") or "未知错误"
            except BskError as e:
                last_err = str(e)
            if attempt < a.retry:
                time.sleep(1.0 + attempt)
        else:
            log(f"[详情 {i}/{len(targets)}] {pub} 失败：{last_err}")
            errors.append({"stage": "detail", "publication_number": pub, "error": last_err})
        if i < len(targets) and a.delay:
            time.sleep(a.delay)

    payload = {
        "ok": bool(details) or bool(hits),
        "source": "patents.google.com",
        "query": outer_query(a),
        "search_url": search_url,
        "total_hits_reported": total,
        "hits_fetched": len(hits),
        "details_fetched": len(details),
        "errors": errors,
        "results": details,
        "hits": hits,
    }
    if a.out:
        emit(payload, a.format, "results", a.out)
    else:
        emit(payload, a.format, "results")
    return 0 if (details or hits) else 1


if __name__ == "__main__":
    sys.exit(main())
