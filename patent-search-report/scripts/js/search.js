/* Google Patents 检索（在页面上下文内执行，同源）
 *
 * 说明：patents.google.com 的检索结果是前端异步渲染的，直接读 DOM 既慢又脆。
 * 它自己调的接口就是 `/xhr/query?url=<编码后的检索式>&exp=`，返回结构化 JSON。
 * 本脚本在 patents.google.com 的页面上下文里 fetch 这个同源接口——
 * 仍然是「在用户的浏览器里、用用户的网络出口（代理/VPN）发起检索」，不绕任何东西。
 *
 * 用法：由 scripts/search.py 读取本文件，注入 __GP_QUERY__ 后交给 `bsk evaluate`。
 *
 * __GP_QUERY__ = {
 *   q: string,                // 检索式（支持 Google Patents 语法，见 references/query-syntax.md）
 *   country: string|string[], // 国家/地区代码，如 "CN"
 *   num: number,              // 每页条数，默认 10，最大 100
 *   page: number,             // 页码，0 起
 *   sort: "new"|"old"|null,   // 按日期排序
 *   before: "priority:YYYYMMDD" | "filing:..." | "publication:...",
 *   after:  "priority:YYYYMMDD" | ...,
 *   status: "GRANT"|"APPLICATION",
 *   type: "PATENT",
 *   language: "CHINESE"|"ENGLISH",
 *   assignee: string, inventor: string,
 *   raw: string               // 直接追加的原始查询串（逃生通道）
 * }
 *
 * 返回：JSON 字符串 {"ok":true,"results":[...],"total":N,...}
 */
(async () => {
  try {
    const Q = __GP_QUERY__ || {};

    const push = (parts, k, v) => {
      if (v === null || v === undefined || v === "") return;
      if (Array.isArray(v)) {
        v.forEach((x) => push(parts, k, x));
      } else {
        parts.push(k + "=" + String(v));
      }
    };

    const parts = [];
    push(parts, "q", Q.q);
    if (Q.raw) parts.push(Q.raw.replace(/^&+|&+$/g, ""));
    push(parts, "country", Q.country);
    push(parts, "language", Q.language);
    push(parts, "status", Q.status);
    push(parts, "type", Q.type);
    push(parts, "assignee", Q.assignee);
    push(parts, "inventor", Q.inventor);
    push(parts, "before", Q.before);
    push(parts, "after", Q.after);
    push(parts, "sort", Q.sort);
    push(parts, "num", Q.num || 10);
    push(parts, "page", Q.page === undefined || Q.page === null ? 0 : Q.page);

    const inner = parts.join("&");
    const url = "/xhr/query?url=" + encodeURIComponent(inner) + "&exp=";

    const resp = await fetch(url, { credentials: "same-origin", headers: { accept: "application/json" } });
    if (!resp.ok) {
      return JSON.stringify({ ok: false, error: "HTTP " + resp.status, request_url: url });
    }
    const j = await resp.json();
    const res = j.results || {};

    const rows = [];
    // 结果里的高亮 <b> 与 &hellip; 之类的实体都要清掉，否则会污染 CSV/表格
    const decodeEnt = (s) => {
      try {
        return new DOMParser().parseFromString(s, "text/html").documentElement.textContent || s;
      } catch (e) {
        return s;
      }
    };
    const strip = (s) => decodeEnt((s || "").replace(/<\/?b>/g, "").replace(/\s+/g, " ")).trim();
    for (const cluster of res.cluster || []) {
      for (const item of cluster.result || []) {
        const p = item.patent || {};
        const fam = (p.family_metadata && p.family_metadata.aggregated) || {};
        rows.push({
          publication_number: p.publication_number || null,
          id: item.id || null,
          url: p.publication_number
            ? "https://patents.google.com/patent/" + p.publication_number + "/" + (p.language || "en")
            : null,
          title: strip(p.title),
          assignee: p.assignee ? strip(p.assignee) : null,
          inventor: p.inventor ? strip(p.inventor) : null,
          priority_date: p.priority_date || null,
          filing_date: p.filing_date || null,
          publication_date: p.publication_date || null,
          language: p.language || null,
          country: p.publication_number ? String(p.publication_number).replace(/[0-9].*$/, "") : null,
          snippet: strip(p.snippet),
          pdf_url: p.pdf ? "https://patentimages.storage.googleapis.com/" + p.pdf : null,
          country_status: fam.country_status || null,
        });
      }
    }

    return JSON.stringify({
      ok: true,
      query: inner,
      request_url: url,
      total: res.total_num_results === undefined ? null : res.total_num_results,
      total_pages: res.total_num_pages === undefined ? null : res.total_num_pages,
      page: res.num_page === undefined ? null : res.num_page,
      count: rows.length,
      results: rows,
    });
  } catch (err) {
    return JSON.stringify({ ok: false, error: String((err && err.stack) || err) });
  }
})()
