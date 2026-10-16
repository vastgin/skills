#!/usr/bin/env python3
"""Google Patents 免费专利检索 —— 检索并取回结果列表（公开号 / 标题 / 申请人 / 日期 ...）。

走用户的真实浏览器（BrowserSkill）在 patents.google.com 页面上下文内发起同源检索，
拿到结构化的结果 JSON，不依赖前端渲染，稳定且快（单页 ≈ 0.7s）。

示例：
  search.py --query "充电桩 散热" --country CN --limit 20
  search.py --assignee "腾讯科技（深圳）有限公司" --country CN --limit 50 --sort new
  search.py --query "锂电池" --after priority:20240101 --type PATENT --status GRANT
  search.py --raw "q=((充电桩)+AND+(散热))" --limit 30 --format csv --out /tmp/hits.csv
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

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_JS = os.path.join(HERE, "js", "search.js")
MAX_PER_PAGE = 100  # Google Patents 硬上限；小于 10 会被服务端按 10 处理


def build_query(a) -> dict:
    """把 argparse 命名空间转成 search.js 的查询对象（search.py / collect.py 共用）。"""
    g = lambda k, d=None: getattr(a, k, d)  # noqa: E731
    return {
        "q": g("query", "") or "",
        "country": g("country") or None,
        "language": g("language") or None,
        "status": g("status") or None,
        "type": g("type") or None,
        "assignee": g("assignee") or None,
        "inventor": g("inventor") or None,
        "before": g("before") or None,
        "after": g("after") or None,
        "sort": g("sort") or None,
        "raw": g("raw") or None,
        "num": MAX_PER_PAGE,
        "page": g("page", 0) or 0,
    }


def outer_query(a) -> str:
    """拼出给人看的检索 URL 里的查询串（不含 num / page，便于浏览器打开）。"""
    parts = []
    if a.raw:
        parts.append(a.raw.strip("&"))
    if a.query:
        parts.append("q=" + a.query)
    for k in ("country", "language", "status", "type", "assignee", "inventor", "before", "after", "sort"):
        v = getattr(a, k, None)
        if not v:
            continue
        if isinstance(v, (list, tuple)):
            parts.extend(f"{k}={x}" for x in v)
        else:
            parts.append(f"{k}={v}")
    return "&".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Google Patents 免费专利检索")
    ap.add_argument("--query", "-q", default="", help="检索式，支持 Google Patents 语法（AND / OR / NOT / 引号短语 / 字段前缀）")
    ap.add_argument("--raw", default="", help="原始查询串（逃生通道，直接拼到 URL query 里）")
    ap.add_argument("--country", default="", help="国家/地区代码，多个用逗号分隔，如 CN 或 CN,US")
    ap.add_argument("--language", default="", help="文献语言：CHINESE / ENGLISH")
    ap.add_argument("--status", default="", help="GRANT（已授权） / APPLICATION（申请）")
    ap.add_argument("--type", default="", help="PATENT / DESIGN ...")
    ap.add_argument("--assignee", default="", help="申请人/专利权人")
    ap.add_argument("--inventor", default="", help="发明人")
    ap.add_argument("--before", default="", help='日期上界，如 "priority:20241231" / "publication:2024" ')
    ap.add_argument("--after", default="", help='日期下界，如 "priority:20240101"')
    ap.add_argument("--sort", default="", choices=["", "new", "old"], help="按日期排序")
    ap.add_argument("--limit", "-n", type=int, default=10, help="最多返回多少条（默认 10；服务端单页上限 100，超出会自动翻页）")
    ap.add_argument("--page", type=int, default=0, help="起始页（0 起）")
    ap.add_argument("--show", dest="show", action="store_true", default=True, help="在浏览器里打开检索结果页（默认开）")
    ap.add_argument("--no-show", dest="show", action="store_false", help="不打开检索页，直接取数据（更快）")
    ap.add_argument("--format", default="json", choices=["json", "ndjson", "csv", "tsv", "table"])
    ap.add_argument("--out", default="", help="结果写文件（json/ndjson/csv/tsv）")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if not (a.query or a.raw or a.assignee or a.inventor):
        ap.error("至少要给 --query / --raw / --assignee / --inventor 之一")

    # 归一逗号分隔的多值
    if a.country:
        a.country = [c.strip().upper() for c in a.country.split(",") if c.strip()]

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

    # 让人能在浏览器里看到这次检索
    if a.show:
        try:
            show_url = "https://patents.google.com/?" + outer_query(a)
            tab = bsk.navigate(session, show_url, tab_id=tab, wait_until="domcontentloaded")
        except BskError as e:
            log(f"（提示：打开检索页失败，继续直接取数：{e}）")

    results: list[dict] = []
    errors: list[str] = []
    total = None
    total_pages = None
    seen: set[str] = set()

    for i in range(pages_needed):
        page = a.page + i
        q = build_query(a)
        q["num"] = page_size
        q["page"] = page
        t0 = time.time()
        try:
            r = bsk.evaluate_file(session, tab, SEARCH_JS, {"__GP_QUERY__": q})
        except BskError as e:
            errors.append(f"page {page}: {e}")
            break
        if not r or not r.get("ok"):
            errors.append(f"page {page}: {(r or {}).get('error')}")
            break
        total = r.get("total")
        total_pages = r.get("total_pages")
        batch = r.get("results") or []
        log(f"[第 {page + 1} 页] 命中 {len(batch)} 条 · 用时 {time.time() - t0:.1f}s · 累计 {min(len(results) + len(batch), limit)}/{limit}")
        for row in batch:
            key = row.get("publication_number") or json.dumps(row, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            results.append(row)
            if len(results) >= limit:
                break
        if len(results) >= limit or not batch:
            break
        if total_pages is not None and page + 1 >= min(total_pages, 100):
            break
        time.sleep(0.4)

    payload = {
        "ok": not errors or bool(results),
        "source": "patents.google.com",
        "query": outer_query(a),
        "search_url": "https://patents.google.com/?" + outer_query(a),
        "total": total,
        "total_pages": total_pages,
        "fetched": len(results),
        "errors": errors,
        "results": results,
    }
    if errors:
        log("；".join(errors))

    if a.out:
        emit(payload, a.format, "results", a.out)
    else:
        emit(payload, a.format, "results")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
