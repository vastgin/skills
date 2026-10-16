/* Google Patents 详情页字段提取（在页面上下文内执行）
 *
 * 为什么不用「直接读渲染后的 DOM」：
 *   patents.google.com 是 Polymer 单页应用，水合完成后会把服务端渲染的语义标记
 *   （itemprop / section / dl 结构）整体替换成它自己的模板 DOM —— 实测渲染后
 *   `document.querySelectorAll('[itemprop]')` 返回空集，且信息缺项（没有 Authority、
 *   没有日期、没有 Original Assignee）。
 *   稳妥做法：在详情页上下文里 `fetch(location.href)` 取回**服务端渲染的 HTML**，
 *   用 DOMParser 解析后再按语义标记抽字段。HTML 完整、结构稳定、无竞态。
 *
 * 用法：由 scripts/detail.py 读取本文件，注入 __GP_OPT__ 后交给 `bsk evaluate`。
 *
 * __GP_OPT__ = {
 *   url: string|null,            // 要抓的 URL，默认 location.href
 *   source: "server-html"|"dom", // 默认 server-html
 *   with_abstract: bool,         // 默认 true
 *   with_claims: bool,           // 默认 false
 *   with_description: bool,      // 默认 false
 *   with_citations: bool,        // 默认 false
 *   with_cited_by: bool,         // 默认 false
 *   with_similar: bool,          // 默认 false
 * }
 *
 * 返回：JSON 字符串 {"ok":true,"data":{...}} 或 {"ok":false,"error":"..."}
 */
(async () => {
  try {
    const OPT = __GP_OPT__ || {};
    const SOURCE = OPT.source || "server-html";

    // ---------- 取文档根 ----------
    let doc = document;
    let htmlLen = 0;
    if (SOURCE === "server-html") {
      const target = OPT.url || location.href;
      const resp = await fetch(target, { credentials: "same-origin" });
      if (!resp.ok) {
        return JSON.stringify({ ok: false, error: "HTTP " + resp.status + " for " + target });
      }
      const text = await resp.text();
      htmlLen = text.length;
      doc = new DOMParser().parseFromString(text, "text/html");
    }

    // ---------- 小工具 ----------
    const T = (e) => (e ? (e.textContent || "").replace(/\s+/g, " ").trim() : "");
    const nz = (v) => (v === "" || v === undefined || v === null ? null : v);
    const q = (s) => doc.querySelector(s);
    const qa = (s) => Array.from(doc.querySelectorAll(s));
    const uniq = (a) => Array.from(new Set(a.filter(Boolean)));
    const metaC = (n) => {
      const e = doc.querySelector('meta[name="' + n + '"]');
      return e ? e.getAttribute("content") : null;
    };
    const val = (e) => {
      if (!e) return null;
      const tm = e.querySelector("time");
      if (tm) return nz(tm.getAttribute("datetime") || T(tm));
      const t = T(e);
      if (t) return t;
      return nz(e.getAttribute("content"));
    };
    const props = (ip) => uniq(qa('[itemprop="' + ip + '"]').map(val));

    // 把内容区拆成「叶子块」并逐块成行（保留段落感，又不吞嵌套）
    const leafBlocks = (root) => {
      const out = [];
      const rec = (el) => {
        const kids = Array.from(el.children || []);
        if (!kids.length) {
          const t = T(el);
          if (t) out.push(t);
          return;
        }
        const blockish = kids.filter((k) => /^(DIV|P|LI|H1|H2|H3|H4|TABLE|SECTION|ARTICLE)$/.test(k.tagName));
        if (blockish.length) {
          for (const k of kids) rec(k);
        } else {
          const t = T(el);
          if (t) out.push(t);
        }
      };
      rec(root);
      const joined = out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
      return nz(joined);
    };
    const blockText = (ip) => {
      const host =
        q('section[itemprop="' + ip + '"] [itemprop="content"]') || q('section[itemprop="' + ip + '"]');
      return host ? leafBlocks(host) : null;
    };
    const rowsOf = (ip, cols) => {
      const out = [];
      for (const tr of qa('tr[itemprop="' + ip + '"]')) {
        const tds = Array.from(tr.querySelectorAll("td"));
        const rec = {};
        cols.forEach((c, i) => {
          const td = tds[i];
          if (!td) return;
          const pn = td.querySelector('[itemprop="publicationNumber"]');
          const link = td.querySelector("a[href*='/patent/']");
          rec[c] = nz(pn ? T(pn) : T(td));
          if (c === "publication_number" && link) rec.href = link.getAttribute("href");
        });
        out.push(rec);
      }
      return out;
    };

    // ---------- 著录信息：全文档 dt/dd 配对 ----------
    // 服务端 HTML 里 Info 区被拆成多个 <dl>，而且有一批 <dt>/<dd> 直接裸露在
    // </dl> 之后（不合法但浏览器容错），所以**不能只遍历 <dl> 的子元素**——
    // 必须按全文档 dt 顺序，往后吃连续的 dd 兄弟节点。标签末尾括号说明会剥掉。
    const info = {};
    for (const dt of qa("dt")) {
      const lbl = T(dt)
        .replace(/\(.*$/, "")
        .replace(/[：:]\s*$/, "")
        .trim();
      if (!lbl) continue;
      const values = [];
      let n = dt.nextElementSibling;
      while (n && n.tagName === "DD") {
        const v = val(n);
        if (v) values.push(v);
        n = n.nextElementSibling;
      }
      if (!(lbl in info)) info[lbl] = [];
      info[lbl].push(...values);
    }
    const info1 = (k) => (info[k] && info[k][0]) || null;
    const infoAll = (k) => uniq(info[k] || []);

    // ---------- 号 & 标题 ----------
    const pubMeta = metaC("citation_patent_publication_number") || "";
    const appMeta = metaC("citation_patent_application_number") || "";
    const publicationNumber = info1("Publication number") || props("publicationNumber")[0] || null;
    const applicationNumber =
      info1("Application number") ||
      props("applicationNumber").find((x) => /^[A-Z]{2}/.test(x)) ||
      (appMeta ? appMeta.replace(/^[A-Z]{2}:?/, "") : null);
    const title = props("title")[0] || null;
    const titleFull = T(q('h1[itemprop="pageTitle"]')).replace(/\s*[-–]\s*Google Patents\s*$/, "");

    // Google 的申请号写法会把种类代码拼在末尾（CN202210114753.XA = 申请号 CN202210114753.X + 种类码 A）。
    // 额外给一个「去国家代码 + 去种类代码」的本体，便于与官方申请号写法对齐。
    const appCore = (() => {
      if (!applicationNumber) return null;
      const m = String(applicationNumber).match(/^([A-Z]{2})(.+?)([A-Z]\d?)$/);
      return m ? m[2] : String(applicationNumber).replace(/^[A-Z]{2}/, "");
    })();

    // ---------- 日期 ----------
    const priorityDate = info1("Priority date") || props("priorityDate")[0] || null;
    const filingDate = info1("Filing date") || props("filingDate")[0] || null;
    const publicationDate = info1("Publication date") || props("publicationDate")[0] || null;

    // ---------- 主体 ----------
    const inventors = infoAll("Inventor");
    const assigneeCurrent = infoAll("Current Assignee");
    const assigneeOriginal = infoAll("Original Assignee");

    // ---------- 法律状态 ----------
    const legalStatus = props("status")[0] || null; // Granted / Pending / Expired ...
    const ifiStatus = props("ifiStatus")[0] || null; // Active / Expired ...
    const priorArtKeywords = infoAll("Prior art keywords");

    // ---------- 分类号 ----------
    const clsSeen = new Set();
    const classifications = [];
    for (const li of qa('[itemprop="classifications"]')) {
      const code = li.querySelector('[itemprop="Code"]');
      if (!code) continue;
      const desc = li.querySelector('[itemprop="Description"]');
      const isCpc = li.querySelector('meta[itemprop="IsCPC"]');
      const rec = {
        code: T(code),
        desc: nz(T(desc)),
        scheme: isCpc && isCpc.getAttribute("content") === "true" ? "CPC" : "IPC",
      };
      const key = rec.scheme + "|" + rec.code;
      if (clsSeen.has(key)) continue;
      clsSeen.add(key);
      classifications.push(rec);
    }

    // ---------- 同族 / 多版本 ----------
    const alsoPublishedAs = rowsOf("pubs", ["publication_number", "publication_date"]);
    const docdbFamily = rowsOf("docdbFamily", ["publication_number", "publication_date"]);
    const countryStatus = rowsOf("countryStatus", ["country", "representative_publication"]);
    const otherLanguages = Array.from(qa('[itemprop="otherLanguages"]')).map((a) => ({
      name: T(a.querySelector('[itemprop="name"]')),
      code: T(a.querySelector('[itemprop="code"]')),
      href: a.getAttribute("href"),
    }));

    // ---------- 引用 ----------
    const citations = rowsOf("backwardReferences", ["publication_number", "priority_date", "publication_date", "assignee", "title"]);
    const citedBy = rowsOf("forwardReferences", ["publication_number", "priority_date", "publication_date", "assignee", "title"]);
    const similarDocuments = rowsOf("similarDocuments", ["publication_number", "publication_date", "title"]);

    // ---------- 法律事件 ----------
    const legalEvents = [];
    for (const tr of qa('tr[itemprop="legalEvents"]')) {
      const cells = Array.from(tr.querySelectorAll("td"));
      const d = tr.querySelector('time[itemprop="date"]');
      legalEvents.push({
        date: d ? d.getAttribute("datetime") || T(d) : null,
        code: nz(T(tr.querySelector('[itemprop="code"]'))),
        title: nz(T(tr.querySelector('[itemprop="title"]'))),
        desc: nz(T(cells[cells.length - 1])),
      });
    }
    const legalEventsUniq = legalEvents.filter(
      (e, i, a) => a.findIndex((x) => x.date === e.date && x.code === e.code && x.title === e.title) === i
    );

    // ---------- 里程碑事件 ----------
    const events = Array.from(qa('[itemprop="events"]')).map((e) => {
      const d = e.querySelector("time");
      const ty = e.querySelector('[itemprop="type"]');
      return {
        date: d ? d.getAttribute("datetime") || T(d) : null,
        title: nz(T(e.querySelector('[itemprop="title"]'))),
        type: ty ? nz(ty.getAttribute("content") || T(ty)) : null,
      };
    });

    // ---------- 授权公告日 ----------
    // Google 的 Info 区**没有**「Grant date」这一项；授权/公告日要从法律事件
    // （GR01 Patent grant）或同族公告（B 文本的 publication date）推。
    const grantFromEvents = (() => {
      const hit = legalEventsUniq.find((x) => /^GR\d*$/i.test(x.code || "") || /grant/i.test(x.title || ""));
      return hit ? hit.date : null;
    })();
    const grantFromPubs = (() => {
      const b = alsoPublishedAs.find((x) => /B\d*$/.test(x.publication_number || ""));
      return b ? b.publication_date : null;
    })();
    const grantDate = info1("Grant date") || grantFromEvents || grantFromPubs || null;

    // ---------- 长文本 ----------
    const abstract = OPT.with_abstract === false ? null : blockText("abstract");
    let claims = null;
    let claimCount = null;
    if (OPT.with_claims) {
      claims = blockText("claims");
      const cEl = q('section[itemprop="claims"] [itemprop="count"]');
      if (cEl) {
        const n = parseInt(T(cEl), 10);
        if (!isNaN(n)) claimCount = n;
      }
      if (claimCount === null && claims) {
        // 兜底：数一下「1. / 1．/ 1、」这类权利要求序号
        const nums = (claims.match(/(?:^|\n)\s*(\d+)\s*[.．、]/g) || []).map((x) => parseInt(x, 10));
        claimCount = nums.length ? Math.max.apply(null, nums) : null;
      }
    }
    const description = OPT.with_description ? blockText("description") : null;

    const data = {
      publication_number: publicationNumber,
      kind_code: (q('meta[itemprop="kindCode"]') || {}).content || (pubMeta ? pubMeta.split(":").pop() : null),
      country_code: props("countryCode")[0] || null,
      authority: info1("Authority"),
      authority_name: props("countryName")[0] || null,
      application_number: applicationNumber,
      application_number_core: appCore,
      representative_publication: props("representativePublication")[0] || null,
      title: title,
      title_full: titleFull,
      abstract: abstract,
      priority_date: priorityDate,
      filing_date: filingDate,
      publication_date: publicationDate,
      grant_date: grantDate,
      inventors: inventors,
      assignee_current: assigneeCurrent,
      assignee_original: assigneeOriginal,
      legal_status: legalStatus,
      ifi_status: ifiStatus,
      prior_art_keywords: priorArtKeywords,
      classifications: classifications,
      citations_count: citations.length,
      cited_by_count: citedBy.length,
      similar_count: similarDocuments.length,
      also_published_as: alsoPublishedAs,
      docdb_family: docdbFamily,
      country_status: countryStatus,
      other_languages: otherLanguages,
      pdf_url: metaC("citation_pdf_url"),
      url: OPT.url || location.href,
      lang: (() => {
        const u = OPT.url || location.href;
        const m = u.match(/\/patent\/[^/]+\/([a-z]{2})(?:[/?#]|$)/i);
        return m ? m[1] : null;
      })(),
      legal_events: legalEventsUniq,
      events: events,
      _meta: { source: SOURCE, html_bytes: htmlLen || null },
    };

    if (OPT.with_citations) data.citations = citations;
    if (OPT.with_cited_by) data.cited_by = citedBy;
    if (OPT.with_similar) data.similar_documents = similarDocuments;
    if (OPT.with_claims) {
      data.claims = claims;
      data.claim_count = claimCount;
    }
    if (OPT.with_description) data.description = description;

    return JSON.stringify({ ok: true, data: data });
  } catch (err) {
    return JSON.stringify({ ok: false, error: String((err && err.stack) || err) });
  }
})()
