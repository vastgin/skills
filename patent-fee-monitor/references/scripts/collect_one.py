#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CNIPA 应缴年费 · 单条端到端采集（v5 · DOM 直取 + async 等待）

设计目标：把「一次采集」从 20+ 次 agent 往返压成 **一次脚本调用**。
实测依据：`bsk evaluate` 单次往返约 130ms，且支持 Promise/async，
因此「填号 → 查询 → 等结果 → 打开详情 → 点费用信息 → 等渲染 → 提取」
全部用页面内 JS 异步轮询完成，不再用 shell sleep 硬等。

用法:
    SESSION=<sid> MAIN_TAB=<tab> python3 collect_one.py <申请号> [--outdir DIR]

输出:
    stdout 最后一行 = 单行 JSON 结果（供调用方解析/播报）
    <outdir>/<申请号>.json = 同内容落盘（供 push_mcp.py 批量上报）

退出码: 0=ok  1=not_found  2=not_granted  3=error(可重试)  4=登录态失效

红线遵守:
    - 只读「应缴费信息」表；已缴清状态整行丢弃；非年费种类丢弃
    - 页面没有的字段不造；feeYear 两级兜底失败整笔丢弃（不传 null）
    - patent_type 只按归一后申请号第 5 位推导，推不出记 problems、不猜
    - 不编造费用数据
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

URL = 'https://cpquery.cponline.cnipa.gov.cn/chinesepatent/index'

# ---- 契约映射表（与服务端 DTO 保持一致） ----
FEE_STATUS = {'未缴': 'UNPAID', '缓缴': 'DEFERRED', '减缴': 'REDUCED'}
PAID_WORDS = ('费足', '已缴', '缴费', '已缴清')
TYPE_CODE = {'1': 1, '2': 2, '3': 3, '8': 1, '9': 2}
TYPE_NAME = {'1': '发明', '2': '实用新型', '3': '外观设计',
             '8': '发明(PCT)', '9': '实用新型(PCT)'}
LEGAL_RULES = [
    (('专利权维持', '专利权有效', '已授权', '授权'), 2),
    (('驳回',), 3),
    (('撤回',), 4),
    (('权利终止', '专利权终止', '终止', '失效', '放弃'), 5),
    (('审中', '实审', '实质审查', '公开', '申请中'), 1),
]
NOT_GRANTED_KW = ('审中', '实审', '实质审查', '公开', '驳回', '撤回', '视为撤回')


def find_bsk():
    """定位 bsk 可执行文件（跨平台）。

    **Windows 要点**：官方 `install.ps1` 把 CLI 装到 `%USERPROFILE%\\.local\\bin\\bsk.exe`
    （可用 `BSK_INSTALL_DIR` 覆盖）。在 Windows 上探测时必须带 `.exe` 后缀，否则找不到，
    就退化成裸命令名 `bsk` —— 而在 PATH 尚未生效的会话里会直接报「找不到命令」。
    路径统一放用户目录下的 `.local/bin`，故 macOS/Linux/Windows 都能用同一个基目录。
    """
    home_bin = os.path.expanduser('~/.local/bin')
    names = (['bsk.exe', 'bsk.cmd', 'bsk.bat', 'bsk'] if os.name == 'nt'
             else ['bsk'])
    for n in names:
        p = os.path.join(home_bin, n)
        if os.path.exists(p):
            return p
    if os.name == 'nt':
        for n in ('bsk.exe', 'bsk.cmd', 'bsk.bat', 'bsk'):
            w = shutil.which(n)
            if w:
                return w
        return 'bsk.exe'
    return shutil.which('bsk') or 'bsk'


BSK = find_bsk()


def _env():
    e = dict(os.environ)
    e['BSK_AUTO_START'] = '0'
    # 前置 bsk 安装目录；os.pathsep 在 Windows 是 ';'，同一行代码两边都对
    e['PATH'] = os.path.expanduser('~/.local/bin') + os.pathsep + e.get('PATH', '')
    return e


def run(*args, timeout=120):
    r = subprocess.run([BSK] + list(args), capture_output=True, text=True,
                       timeout=timeout, env=_env())
    return (r.stdout or '').strip(), (r.stderr or '').strip(), r.returncode


def ev(session, tab, js, timeout=120):
    out, err, rc = run('evaluate', '--session', session, '--tab-id', str(tab),
                       js, timeout=timeout)
    return out


def jload(s):
    """从 evaluate 输出里稳健地取出 JSON 对象。"""
    s = (s or '').strip()
    if not s:
        return None
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j < i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


# ============ 页面内脚本 ============

# 入口页：回读校验 → 点查询 → 轮询结果数（含遮罩判断与重试）。
# 填值走 `bsk fill`（真实事件，前端受控组件认；JS setter 会被 Vue 忽略）。
ENTRY_JS = r'''
(async () => {
  const NUM = '__NUM__';
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const inp = document.querySelector('input[placeholder^="例如: 2010"]');
  if (!inp) return JSON.stringify({ err: 'NO_INPUT' });

  for (let i = 0; i < 8 && inp.value !== NUM; i++) await sleep(150);
  if (inp.value !== NUM) return JSON.stringify({ err: 'FILL_FAIL', val: inp.value });

  const btn = [...document.querySelectorAll('button')].find(
      b => /bg-primary/.test(b.className) && b.innerText.includes('查询'));
  if (!btn) return JSON.stringify({ err: 'NO_BTN' });

  const masked = () => !!document.querySelector(
      '.q-loading, .q-loading__backdrop, .el-loading-mask');
  const count = () => {
    const m = document.body.innerText.match(/共查询到\s*(\d+)\s*条结果/);
    return m ? parseInt(m[1], 10) : -1;
  };

  for (let attempt = 0; attempt < 3; attempt++) {
    btn.click();
    for (let i = 0; i < 16; i++) {          // 每次最多 4s
      await sleep(250);
      const txt = document.body.innerText;
      if (/统一身份认证|手机号\/证件号码/.test(txt)) {
        return JSON.stringify({ err: 'LOGIN_REQUIRED' });
      }
      if (masked()) continue;               // 遮罩期只等待
      const n = count();
      if (n > 0) {
        // 列表首行文本里带「授权公告日：YYYY-MM-DD」，从结果行容器里取
        let grant = '';
        const title = document.querySelector('span.title.hover_active');
        if (title) {
          let el = title;
          for (let k = 0; k < 8 && el; k++) {
            const m = (el.innerText || '')
                .match(/授权公告日[：:]\s*(\d{4}-[01]\d-[0-3]\d)/);
            if (m) { grant = m[1]; break; }
            el = el.parentElement;
          }
        }
        return JSON.stringify({ ok: true, count: n, grantDate: grant });
      }
    }
    await sleep(500);                        // 0 条：可能点击没落地，重做点击
  }
  return JSON.stringify({ ok: true, count: 0 });
})()
'''

def detail_tabs(session, exclude_main=None):
    """当前所有「agent scope 且 URL 含 /detail/」的标签页 id（绝不碰用户标签）。

    `exclude_main` 传主标签 id：主标签在「同标签页打开详情」模式下 URL 也会含
    /detail/，必须排除，否则会被 `close_detail_tabs` 误关（实测该模式引入的坑）。
    """
    out, _, _ = run('tab', 'list', '--session', session, '--json')
    ids = []
    try:
        for t in json.loads(out).get('tabs', []):
            if t.get('scope') != 'agent':
                continue
            if exclude_main is not None and str(t.get('tab_id')) == str(exclude_main):
                continue
            if '/detail/' in (t.get('url') or ''):
                ids.append(str(t['tab_id']))
    except Exception:
        pass
    return ids


def close_detail_tabs(session, exclude_main=None):
    """关闭所有残留详情页标签。

    这是**串扰的根源**：上一条没关干净的详情页会被本条当成自己的页面读走，
    表现为「申请号串扰」或「NO_FEE_NODE」。每条开始前必清。
    """
    n = 0
    for tid in detail_tabs(session, exclude_main=exclude_main):
        run('tab', 'close', '--session', session, tid)
        n += 1
    return n


def find_detail_tab(session, exclude=(), tries=20, gap=0.5):
    """【已弃用，保留供排查】找**新出现**的详情页标签。

    2026-10-13 起详情页改为「同标签页」打开（见 `wait_detail`），不再新开标签，
    故主流程已不调用本函数。留着是为了站点万一改回新标签模式时便于对照排查。

    历史说明：JS `element.click()` 是合成事件，该站点不认、不会开详情页；
    必须走 bsk click 派发真实鼠标事件。
    """
    exclude = {str(x) for x in exclude}
    for _ in range(tries):
        for tid in detail_tabs(session):
            if tid not in exclude:
                return tid
        time.sleep(gap)
    return None


# ============ 不抢焦点地打开详情页（2026-10-13） ============
#
# 问题：站点点击案件标题时用 JS **另开标签页**打开详情，这会让 Chrome 窗口冲到
# 前台，把用户正在做的事打断一次。`bsk session start --no-focus` 只管「建会话那
# 一刻」，管不到运行期新开标签页；bsk 也没有 hide/minimize 能力。
#
# 解法：点击前把页面的 `window.open` 覆盖掉，改成「当前标签页跳转」。
# 于是详情页在**同一个标签页**里加载 —— 不新建标签、不激活窗口、**不抢焦点**，
# 一次解决而不是反复抢回来。
#
# 为什么不能靠改 `target=_self`：实测无效，说明站点是显式调 `window.open`。
# 为什么覆盖后仍保留「新标签页」分支：万一站点改版，仍能靠兜底路径采到数据。
PATCH_WINDOW_OPEN_JS = r'''(() => {
  if (window.__bskPatchedOpen) return 'already';
  window.__bskPatchedOpen = true;
  window.__bskRealOpen = window.open;
  window.open = function (u) {
    try {
      if (typeof u === 'string' && u) { window.location.href = u; return null; }
    } catch (e) {}
    return null;
  };
  return 'patched';
})()'''


def wait_detail(session, main_tab, before=(), tries=25, gap=0.4):
    """点击标题后等详情页出现，返回 (tab_id, how)。

    how = 'same' 详情页在当前标签页打开（预期路径，不抢焦点）
    how = 'new'  另开了新标签页（覆盖失效时的兜底路径，需要读完关掉）
    """
    before = {str(x) for x in before}
    for _ in range(tries):
        out, _, _ = run('tab', 'list', '--session', session, '--json')
        try:
            tabs = json.loads(out).get('tabs', [])
        except Exception:
            tabs = []
        for t in tabs:
            if str(t.get('tab_id')) == str(main_tab) and '/detail/' in (t.get('url') or ''):
                return str(main_tab), 'same'
        for t in tabs:
            tid = str(t.get('tab_id'))
            if (t.get('scope') == 'agent' and '/detail/' in (t.get('url') or '')
                    and tid not in before):
                return tid, 'new'
        time.sleep(gap)
    return None, None

# 详情页：先抓「申请信息」默认视图（著录）→ 再点「费用信息」抓费用表。
# 关键：Element UI 树是**按需渲染当前面板**，同一时刻只有一个面板在 DOM 里，
# 所以著录必须在切到费用信息**之前**取，否则拿不到发明名称/申请日/申请人。
DETAIL_JS = r'''
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const masked = () => !!document.querySelector(
      '.q-loading, .q-loading__backdrop, .el-loading-mask');
  const grab = () => ({
    text: document.body.innerText.slice(0, 8000),
    tables: [...document.querySelectorAll('table')].map(t => ({
      head: [...t.querySelectorAll('thead th')].map(h => h.innerText.trim()),
      rows: [...t.querySelectorAll('tbody tr')].map(
          r => [...r.querySelectorAll('td')].map(c => c.innerText.trim()))
    }))
  });

  // 0) 先等 SPA 把内容渲染出来（冷开新标签时初始 DOM 是空的）
  for (let i = 0; i < 60; i++) {
    if ((document.body.innerText || '').length > 200) break;
    await sleep(250);
  }

  // 1) 等「费用信息」树节点出现（冷开 SPA 偶发要十几秒）
  let node = null;
  for (let i = 0; i < 60; i++) {
    node = [...document.querySelectorAll('[role=treeitem]')].find(
        e => e.innerText.trim() === '费用信息');
    if (node) break;
    await sleep(250);
  }
  if (!node) return JSON.stringify({ err: 'NO_FEE_NODE',
      url: location.href, peek: (document.body.innerText || '').slice(0, 400) });

  // 2) 先抓默认（申请信息）视图的著录；若迟迟没渲染，主动点「申请信息」节点再等
  let info = null;
  for (let round = 0; round < 2 && !info; round++) {
    if (round === 1) {
      const n2 = [...document.querySelectorAll('[role=treeitem]')].find(
          e => e.innerText.trim() === '申请信息');
      if (n2) (n2.querySelector('.el-tree-node__content') || n2).click();
    }
    for (let i = 0; i < 32; i++) {
      await sleep(250);
      if (masked()) continue;
      const t = document.body.innerText;
      if (t.includes('发明名称') || t.includes('案件状态') || t.includes('申请日')) {
        info = grab();
        break;
      }
    }
  }

  // 3) 切到「费用信息」，等表渲染
  (node.querySelector('.el-tree-node__content') || node).click();
  let ready = false;
  for (let i = 0; i < 48; i++) {
    await sleep(250);
    if (masked()) continue;
    if (document.body.innerText.includes('应缴费信息')) { ready = true; break; }
  }
  if (!ready) return JSON.stringify({ err: 'NO_FEE_TABLE' });
  await sleep(300);
  const fee = grab();

  return JSON.stringify({ ok: true, info: info, fee: fee });
})()
'''


# ============ 结果解析 ============

def norm_no(n):
    return (n or '').strip().upper().replace('CN', '').replace('-', '') \
        .replace('.', '').replace(' ', '')


def fld(txt, label):
    """从 innerText 取「标签 → 下一行值」。"""
    m = re.search(re.escape(label) + r'[：:]?\s*\n\s*([^\n]+)', txt)
    return m.group(1).strip() if m else None


def legal_status(case_status, law_status):
    for src in (case_status, law_status):
        if not src:
            continue
        for keys, code in LEGAL_RULES:
            if any(k in src for k in keys):
                return code
    return 0


def to_num(s):
    """金额文本 → float；非法返回 0.0。"""
    t = re.sub(r'[^\d.]', '', s or '')
    try:
        return float(t)
    except Exception:
        return 0.0


def cn_date(s):
    """「2026年10月01日」→「2026-10-01」；解析不出返回空串。"""
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s or '')
    if not m:
        return ''
    return '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def parse_ladder(tbl):
    """解析「应缴滞纳金信息」表 → (ladder, principal)。

    该表表头是 `缴费时间 | 当前年费金额 | 应缴滞纳金额 | 总计`，**不是**契约里的
    四个列。必须：
      ① 把「2026年10月01日到2026年10月30日」**拆成** windowStart/windowEnd 两个日期；
      ② lateFee 取「应缴滞纳金额」列、total 取「总计」列（原实现按位置硬塞，
         把中文时段文本当成了 windowStart，服务端解析日期时 502）。
    列序按表头文字定位，找不到再退回位置默认值。

    返回的 principal 是「当前年费金额」—— 用它把阶梯挂到正确的年度行上
    （该表是整案级的，不含年度信息）。
    """
    if not tbl:
        return [], None
    head = tbl.get('head') or []

    def find(*keys):
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return None

    i_win = find('缴费时间', '时段', '期间')
    i_late = find('滞纳')
    i_tot = find('总计', '合计')
    i_pri = find('年费金额', '本金', '年费')
    if i_win is None:
        i_win = 0
    if i_late is None:
        i_late = 2
    if i_tot is None:
        i_tot = 3

    ladder, principal = [], None
    for r in (tbl.get('rows') or []):
        if len(r) <= max(i_late, i_tot, i_win):
            continue
        parts = re.split(r'\s*到\s*', r[i_win] or '')
        ws = cn_date(parts[0]) if parts else ''
        we = cn_date(parts[1]) if len(parts) > 1 else ''
        if not ws:
            continue
        ladder.append({'windowStart': ws, 'windowEnd': we,
                       'lateFee': to_num(r[i_late]), 'total': to_num(r[i_tot])})
        if principal is None and i_pri is not None and len(r) > i_pri:
            principal = to_num(r[i_pri])
    return ladder, principal


def pick_tables(tables):
    """按表头文字识别各类表，列序变化不影响。

    - fee  ：「应缴费信息」表 —— 费用种类|应缴金额|缴费截止日|费用状态
    - paid ：「已缴费信息」表 —— 费用种类|应缴金额|缴费日期|缴费人姓名|…
             （与 fee 的区别：有「缴费日期」而无「缴费截止日」）
    """
    out = {'fee': None, 'paid': None, 'late': None, 'holder': None}
    for t in tables:
        h = ','.join(t.get('head') or [])
        if '费用种类' in h and '缴费截止日' in h and out['fee'] is None:
            out['fee'] = t
        elif '费用种类' in h and '缴费日期' in h and out['paid'] is None:
            out['paid'] = t
        elif '滞纳金' in h and out['late'] is None:
            out['late'] = t
        elif '姓名或名称' in h and out['holder'] is None:
            out['holder'] = t
    return out


def build(num, info, fee, grant_date=''):
    """把页面原始内容翻译成 payload-ready 的 patent dict。

    info  = 「申请信息」视图（著录项 + 申请人表）
    fee   = 「费用信息」视图（应缴费信息表 + 已缴费信息表 + 滞纳金表）
    grant_date = 搜索结果列表行里的「授权公告日」
    """
    num = norm_no(num)
    itxt = (info or {}).get('text') or ''
    itabs = (info or {}).get('tables') or []
    ftxt = (fee or {}).get('text') or ''
    t = pick_tables((fee or {}).get('tables') or [])
    ti = pick_tables(itabs)
    problems, dropped = [], []

    # --- 案级 ---
    m = re.search(r'申请号/专利号[：:]?\s*\n?\s*(\d{12}[0-9X]?)', ftxt or itxt)
    page_no = norm_no(m.group(1)) if m else None

    name = fld(itxt, '发明名称')
    apply_date = fld(itxt, '申请日')
    if apply_date and not re.match(r'^\d{4}-\d{2}-\d{2}$', apply_date):
        apply_date = None
    case_status = fld(itxt, '案件状态')
    law_status = fld(itxt, '法律状态')

    holder = None
    if ti['holder'] and ti['holder']['rows']:
        names = [r[0] for r in ti['holder']['rows'] if r and r[0]]
        if names:
            holder = '、'.join(dict.fromkeys(names))

    if not name:
        problems.append('发明名称缺失')
    if not apply_date:
        problems.append('申请日缺失')
    if not holder:
        problems.append('权利人/申请人缺失（holderNames 留空）')

    digit = num[4:5]
    if digit not in TYPE_CODE:
        problems.append('申请号第 5 位=%r 无法推导专利类型（禁止猜，需人工确认）' % digit)

    # --- 未缴年费：应缴费信息表 → 按缴费截止日**升序**取前 2 ---
    unpaid, seen = [], set()

    for r in (t['fee']['rows'] if t['fee'] else []):
        if len(r) < 4:
            continue
        kind, amount_raw, due, status = r[0], r[1], r[2], r[3]

        if '滞纳金' not in kind and '年费' not in kind:
            dropped.append('%s：费用种类非年费，按交付边界丢弃' % kind)
            continue
        if status not in FEE_STATUS:
            if any(w in status for w in PAID_WORDS):
                dropped.append('%s %s元/%s：状态「%s」＝已缴清，无应缴义务，正常丢弃'
                               % (kind, amount_raw, due, status))
            else:
                dropped.append('%s %s元/%s：状态「%s」不在服务端枚举内，整行丢弃'
                               % (kind, amount_raw, due, status))
            continue

        my = re.search(r'第\s*(\d+)\s*年', kind)
        fy = int(my.group(1)) if my else None
        if fy is None:
            dropped.append('%s：无法解析年度，整笔丢弃（禁止传 null）' % kind)
            continue

        amt = re.sub(r'[^\d.]', '', amount_raw)
        try:
            amt_f = float(amt)
        except Exception:
            amt_f = -1
        if not (0 < amt_f <= 1000000):
            dropped.append('%s：金额非法（%s），整笔丢弃' % (kind, amount_raw))
            continue

        key = '%s|%s|%s' % (num, kind, due)
        if key in seen:
            continue
        seen.add(key)
        unpaid.append({
            'feeYear': fy,
            'amountDue': amt_f,
            'amountRaw': amount_raw,
            'lateDueDate': due,
            'feeStatus': FEE_STATUS[status],
            'paidAmount': 0,
            'lateFeeLadder': [],
            'dedupKey': key,
        })

    unpaid.sort(key=lambda f: f['lateDueDate'])          # 截止日升序
    truncated = []
    if len(unpaid) > 2:
        truncated.append('未缴年费 %d 条中仅取截止日最早的 2 条（其余 %d 条按规则不上报）'
                         % (len(unpaid), len(unpaid) - 2))
    fees = unpaid[:2]

    # --- 已缴年费：已缴费信息表 → 按缴费日期**降序**取前 2 ---
    # 注意：该表**没有「缴费截止日」列**（列序：费用种类|应缴金额|缴费日期|缴费人姓名|票据代码|票据号码），
    # 故 lateDueDate 留空（不编造），排序与 dedupKey 都基于页面唯一的日期字段「缴费日期」。
    # 列位置按**表头文字**定位，列序变化不影响。
    paid = []
    ph = (t['paid'] or {}).get('head') or []

    def col(name, default=None):
        for i, h in enumerate(ph):
            if name in h:
                return i
        return default

    i_pay = col('缴费日期', 2)
    i_inv = col('票据号码')
    i_due = col('缴费截止日')          # 该表通常没有，有则优先用

    for r in (t['paid']['rows'] if t['paid'] else []):
        if len(r) <= i_pay:
            continue
        kind = r[0]
        amount_raw = r[1] if len(r) > 1 else ''
        pay_date = r[i_pay] if len(r) > i_pay else ''
        invoice_no = r[i_inv] if (i_inv is not None and len(r) > i_inv) else ''
        due_date = r[i_due] if (i_due is not None and len(r) > i_due) else ''
        if '年费' not in kind:
            continue                                     # 只采年费
        if '滞纳金' in kind:
            continue                                     # 年费滞纳金不算「已缴年费」
        my = re.search(r'第\s*(\d+)\s*年', kind)
        fy = int(my.group(1)) if my else None
        if fy is None:
            dropped.append('%s：已缴行无法解析年度，丢弃' % kind)
            continue
        amt = re.sub(r'[^\d.]', '', amount_raw)
        try:
            amt_f = float(amt)
        except Exception:
            amt_f = -1
        if not (0 < amt_f <= 1000000):
            dropped.append('%s：已缴金额非法（%s），丢弃' % (kind, amount_raw))
            continue
        paid.append({
            'feeYear': fy,
            'amountDue': amt_f,
            'amountRaw': amount_raw,
            'lateDueDate': due_date,       # 页面无此列时为空串（不编造）
            'feeStatus': 'PAID',
            'paidAmount': amt_f,
            'lateFeeLadder': [],
            'paidTime': pay_date,          # 缴费日期（服务端字段名就叫 paidTime，勿自造别名）
            'voucherNo': invoice_no,       # 票据号码（凭证）—— 与服务端 DTO 同名，无需映射
            'dedupKey': '%s|%s|%s' % (num, kind, pay_date or due_date or fy),
            '_payDate': pay_date,
        })

    paid.sort(key=lambda f: f['_payDate'], reverse=True)  # 缴费日期降序
    if len(paid) > 2:
        truncated.append('已缴年费 %d 条中仅取缴费日期最近的 2 条（其余 %d 条按规则不上报）'
                         % (len(paid), len(paid) - 2))
    paid = paid[:2]
    for f in paid:
        f.pop('_payDate', None)
    fees += paid

    # --- 滞纳金阶梯：官方表是**整案级**的（不含年度），挂到「当前欠费年度」那一行 ---
    # 依据：该表的「当前年费金额」与最早未缴年度的应缴金额吻合（5 例实测全中）。
    ladder, principal = parse_ladder(t['late'])
    ladder_year = None
    if ladder:
        target = None
        if principal is not None:
            for f in fees:
                if f['feeStatus'] != 'PAID' and abs(f['amountDue'] - principal) < 0.01:
                    target = f
                    break
        if target is None:
            target = next((f for f in fees if f['feeStatus'] != 'PAID'), None)
            if target is not None:
                problems.append('滞纳金窗口未能按金额(¥%s)匹配年度，已挂到最早未缴年度 第%d年'
                                % (principal, target['feeYear']))
        if target is not None:
            target['lateFeeLadder'] = ladder
            ladder_year = target['feeYear']

    patent = {
        'applyNo': num,
        'applyDate': apply_date,
        'name': name,
        'grantDate': grant_date or '',
        'queryStatus': 'ok',
        'failReason': '',
        'legalStatus': legal_status(case_status, law_status),
        'holderNames': holder,
        'type': TYPE_CODE.get(digit),
        'fees': fees,
    }
    meta = {
        'pageApplyNo': page_no,
        'caseStatus': case_status,
        'lawStatus': law_status,
        'typeName': TYPE_NAME.get(digit),
        'problems': problems,
        'dropped': dropped,
        'truncated': truncated,
        'feeRows': len(t['fee']['rows']) if t['fee'] else 0,
        'paidRows': len(t['paid']['rows']) if t['paid'] else 0,
        'amount': round(sum(f['amountDue'] for f in fees), 2),
        'years': [f['feeYear'] for f in fees],
        'unpaid': len([f for f in fees if f['feeStatus'] != 'PAID']),
        'paid': len([f for f in fees if f['feeStatus'] == 'PAID']),
        'ladderYear': ladder_year,
        'ladderWindows': len(ladder),
    }
    return patent, meta


# ============ 主流程 ============

def collect(num, session, tab, outdir):
    t0 = time.time()
    num = norm_no(num)
    res = {'applyNo': num}

    # 0) 清理上一条可能残留的详情页（串扰根源：残留 tab 会被本条读走）
    #    exclude_main：同标签页模式下主标签 URL 也可能含 /detail/，别误关
    close_detail_tabs(session, exclude_main=tab)

    # 1) 确保停在入口页
    ok = ev(session, tab, "!!document.querySelector('input[placeholder^=\"例如: 2010\"]')")
    if ok.strip() != 'true':
        run('navigate', '--session', session, '--tab-id', str(tab), URL)
        time.sleep(2.5)

    # 2) 填号（真实事件，前端认）→ 查询 + 等结果（页面内一次完成）
    run('fill', '--session', session, '--tab-id', str(tab),
        '--selector', 'input[placeholder^="例如: 2010"]', '--value', num)
    r = jload(ev(session, tab, ENTRY_JS.replace('__NUM__', num), timeout=90))
    if not r:
        res.update(queryStatus='error', failReason='RETRYABLE:入口页脚本无返回', fees=[])
        return res, 3
    if r.get('err') == 'LOGIN_REQUIRED':
        res.update(queryStatus='error', failReason='登录态失效', fees=[])
        return res, 4
    if r.get('err'):
        res.update(queryStatus='error',
                   failReason='RETRYABLE:入口页 %s' % r['err'], fees=[])
        return res, 3

    if not r.get('count'):
        res.update(queryStatus='not_found', failReason='查无此申请号', fees=[])
        res['elapsed'] = round(time.time() - t0, 1)
        return res, 1

    # 落在同一个 applyNo 上的授权公告日（列表行里带）
    grant_date = (r.get('grantDate') or '').strip()

    # 3) 等结果行渲染，再真实点击打开详情
    #    （合成事件站点不认；点太早标题还没渲染会静默落空 → 详情页标签不出现）
    for _ in range(20):
        if ev(session, tab, "!!document.querySelector('span.title.hover_active')"
              ).strip() == 'true':
            break
        time.sleep(0.3)

    # 4~5) 开详情 + 提取。**同标签页打开，不抢焦点**（见 PATCH_WINDOW_OPEN_JS 说明）。
    #      详情页是 SPA，冷开偶发渲染不全（NO_FEE_NODE），故最多重试 3 次自愈。
    d = None
    last_reason = '详情页未打开'
    for attempt in (1, 2, 3):
        if attempt > 1:
            # 同标签页模式下，上一轮失败后主标签还停在详情页 —— 需先回到入口页重查
            back = ev(session, tab,
                      "!!document.querySelector('input[placeholder^=\"例如: 2010\"]')")
            if back.strip() != 'true':
                run('navigate', '--session', session, '--tab-id', str(tab), URL)
                time.sleep(2.5)
                run('fill', '--session', session, '--tab-id', str(tab),
                    '--selector', 'input[placeholder^="例如: 2010"]', '--value', num)
                ev(session, tab, ENTRY_JS.replace('__NUM__', num), timeout=90)
                for _ in range(20):
                    if ev(session, tab,
                          "!!document.querySelector('span.title.hover_active')"
                          ).strip() == 'true':
                        break
                    time.sleep(0.3)

        before = set(detail_tabs(session, exclude_main=tab))
        ev(session, tab, PATCH_WINDOW_OPEN_JS)      # 让详情页在**当前标签**打开
        run('click', '--session', session, '--tab-id', str(tab),
            '--selector', 'span.title.hover_active')
        det, how = wait_detail(session, tab, before)
        if not det:
            last_reason = '详情页未打开'
            time.sleep(2.0)
            continue
        raw = ev(session, det, DETAIL_JS, timeout=90)
        d = jload(raw)
        if os.environ.get('COLLECT_DEBUG'):
            os.makedirs(outdir, exist_ok=True)
            open(os.path.join(outdir, 'raw_%s_%d.txt' % (num, attempt)), 'w',
                 encoding='utf-8').write(raw or '(empty)')
        if how == 'new':
            run('tab', 'close', '--session', session, det)   # 兜底路径：读完即关
        close_detail_tabs(session, exclude_main=tab)          # 兜底：无残留再进下一条
        if d and not d.get('err'):
            break
        last_reason = '详情页 %s' % ((d or {}).get('err') or '脚本无返回')
        d = None
        time.sleep(2.0)

    if not d:
        res.update(queryStatus='error',
                   failReason='RETRYABLE:' + last_reason, fees=[])
        return res, 3

    # 6) 解析 + 串扰校验
    if os.environ.get('COLLECT_DEBUG'):
        os.makedirs(outdir, exist_ok=True)
        for tag in ('info', 'fee'):
            blk = d.get(tag) or {}
            open(os.path.join(outdir, '%s_%s.txt' % (tag, num)), 'w',
                 encoding='utf-8').write(blk.get('text') or '')
            json.dump(blk.get('tables'), open(
                os.path.join(outdir, 'tables_%s_%s.json' % (tag, num)), 'w',
                encoding='utf-8'), ensure_ascii=False, indent=1)
    patent, meta = build(num, d.get('info'), d.get('fee'), grant_date)
    if meta['pageApplyNo'] and meta['pageApplyNo'] != num:
        res.update(queryStatus='error',
                   failReason='RETRYABLE:详情页申请号串扰(%s)' % meta['pageApplyNo'],
                   fees=[])
        return res, 3

    # 7) 未授权判定
    cs = meta.get('caseStatus') or ''
    if any(k in cs for k in NOT_GRANTED_KW):
        res.update(queryStatus='not_granted',
                   failReason='案件状态「%s」：尚无年费义务' % cs, fees=[])
        res['elapsed'] = round(time.time() - t0, 1)
        return res, 2

    if meta['feeRows'] > 50:
        res.update(queryStatus='error',
                   failReason='RETRYABLE:应缴明细异常(%d 行)' % meta['feeRows'], fees=[])
        return res, 3

    res.update(patent)
    res['meta'] = meta
    res['elapsed'] = round(time.time() - t0, 1)
    return res, 0


def ensure_session():
    """返回 (session_id, created)。**优先复用本脚本标记过的静默会话**，新建时必须带 `--no-focus`。

    ## 为什么必须 `--no-focus`（2026-10-13 实测定位）

    实测（用 CoreGraphics 免授权监控前后台 App）：
      - `bsk session start`（**不带** `--no-focus`）→ 焦点立刻
        `WorkBuddy → Google Chrome` 来回跳，**持续打断用户手头的事**；
      - `bsk session start --no-focus` → 建会话、click、fill、navigate、全程采集，
        焦点**始终停在 WorkBuddy**，Chrome 完全不抢。

    即 `--no-focus` 只在**建会话那一刻**生效；会话建好之后，`click` / `fill` /
    `navigate` 等动作**都不会**再抢焦点。所以唯一要守住的点是「建会话必须带 `--no-focus`」。

    ## 为什么优先复用

    `bsk session start` 每次都**新开一个 Agent Window**，反复新建 = 反复弹窗。
    但注意 **daemon 会自动回收空闲会话**（日志 `idle session stopped`），
    间隔久了会话就没了，下次仍会重新建窗口 —— 所以本函数必须**永远**带 `--no-focus`，
    不能因为「这次是复用」就假定安全。

    ## 为什么不用「任意已有会话」一刀切复用

    存活会话可能是用户自己或历史误建、**没带 `--no-focus`** 的窗口，复用它等于继续被抢。
    这里只复用**本脚本以 `--no-focus` 建立并标记过**的会话，否则一律新建静默会话。
    """
    marker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '.bsk_quiet_session')
    out, _, _ = run('status', '--json')
    try:
        ss = json.loads(out).get('sessions') or []
    except Exception:
        ss = []

    # 只复用「本脚本以 --no-focus 建立并标记过」的会话
    try:
        marked = open(marker, encoding='utf-8').read().strip()
    except Exception:
        marked = ''
    if marked and any(str(s.get('session_id')) == marked for s in ss):
        return marked, False

    o, _, _ = run('session', 'start', '--no-focus', '--json')
    try:
        d = json.loads(o)
        sid = str(d.get('session_id') or d.get('sessionId') or d.get('id') or '')
    except Exception:
        sid = (o.strip().splitlines()[-1].strip() if o.strip() else '')
    if sid:
        try:
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write(sid)
        except Exception:
            pass
    return sid, True


def auto_session():
    """兼容旧签名：只返回 session_id。"""
    return ensure_session()[0]


def stop_session(session):
    """关闭会话（连带关闭其 Agent Window）—— 采集完想立刻清场时用。"""
    return run('session', 'stop', session)


def auto_tab(session):
    """未显式指定时，取第一个 agent scope 标签页。"""
    out, _, _ = run('tab', 'list', '--session', session, '--json')
    try:
        for t in json.loads(out).get('tabs', []):
            if t.get('scope') == 'agent':
                return str(t['tab_id'])
    except Exception:
        pass
    return ''


def format_line(res, rc):
    """人类可读的一行进度（供调用方逐条播报）。"""
    meta = res.get('meta') or {}
    sym = {0: '✓', 1: '✗', 2: '✗', 3: '⚠', 4: '⚠'}.get(rc, '?')
    y = meta.get('years') or []
    yr = '%d–%d' % (y[0], y[-1]) if y else '-'
    nm = res.get('name') or ''
    nm = nm[:12] + '…' if len(nm) > 12 else nm
    body = nm or (res.get('failReason') or '')
    if rc == 0:
        nu = meta.get('unpaid', 0)
        np_ = meta.get('paid', 0)
        return '%s %s · %s · 未缴%d+已缴%d 条 ¥%s · 年度 %s · %ss' % (
            sym, res['applyNo'], body, nu, np_,
            format(meta.get('amount', 0), ',.0f'), yr, res.get('elapsed', '?'))
    return '%s %s · %s' % (sym, res['applyNo'], body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('applyNo')
    ap.add_argument('--outdir', default='out')
    a = ap.parse_args()

    if os.environ.get('SESSION'):
        session = os.environ['SESSION']
    else:
        session, _created = ensure_session()
    tab = os.environ.get('MAIN_TAB') or (auto_tab(session) if session else '')
    if not session or not tab:
        print('未找到可用会话/标签页：请确认 daemon 在运行，或显式设置 '
              'SESSION 与 MAIN_TAB', file=sys.stderr)
        return 9

    res, rc = collect(a.applyNo, session, tab, a.outdir)
    os.makedirs(a.outdir, exist_ok=True)
    fp = os.path.join(a.outdir, '%s.json' % norm_no(a.applyNo))
    json.dump(res, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(format_line(res, rc))
    meta = res.get('meta') or {}
    if meta.get('problems'):
        print('   ⚠ 字段异常: ' + '；'.join(meta['problems']))
    print('JSON ' + fp)
    return rc


if __name__ == '__main__':
    sys.exit(main())
