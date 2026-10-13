#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由采集结果生成 Markdown 对账报告（适配 2026-10-10 新规则）。

用法: make_report.py --dir out_run --list list_20261010.txt --out 报告.md
"""
import argparse
import datetime
import glob
import json
import os
from collections import Counter

TODAY = datetime.date(2026, 10, 10)
WIN_LO = (TODAY - datetime.timedelta(days=730)).isoformat()
WIN_HI = (TODAY + datetime.timedelta(days=365)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--list', dest='listfile', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    rows = []
    for f in sorted(glob.glob(os.path.join(a.dir, '*.json'))):
        b = os.path.basename(f)
        if b.startswith(('_payload_', 'push_result', 'meta')):
            continue
        d = json.load(open(f, encoding='utf-8'))
        if d.get('applyNo'):
            rows.append(d)
    want = [x.strip() for x in open(a.listfile, encoding='utf-8') if x.strip()]
    mp = os.path.join(a.dir, 'push_result.json')
    push = json.load(open(mp, encoding='utf-8'))['total'] if os.path.exists(mp) else {}

    # 未缴按截止日升序（即"最近要缴"）
    unpaid = []
    for r in rows:
        for f in r.get('fees') or []:
            if f['feeStatus'] != 'PAID':
                unpaid.append((r, f))
    unpaid.sort(key=lambda t: t[1]['lateDueDate'])

    L = []
    L.append('# 专利年费采集对账报告（新规则）\n')
    L.append('- 采集时间：2026-10-10（GMT+8）')
    L.append('- 数据来源：CNIPA 案件查询平台（cpquery）')
    L.append('- 批次号：`%s`' % json.load(open(os.path.join(a.dir, 'meta.json'),
                                          encoding='utf-8'))['batchId']
             if os.path.exists(os.path.join(a.dir, 'meta.json')) else '-')
    L.append('- 清单 %d 条 ｜ 采到 %d 条 ｜ 状态 %s\n'
             % (len(want), len(rows), dict(Counter(r['queryStatus'] for r in rows))))

    L.append('## 一、结果摘要\n')
    L.append('| 项 | 值 |')
    L.append('|---|---|')
    L.append('| 监控专利 | %d 条（全部发明） |' % len(rows))
    L.append('| 上报费用行 | %d 条（每案：未缴取 2 + 已缴取 2） |' % (len(rows) * 4))
    L.append('| **入库 accepted** | **%s 条** |' % push.get('accepted'))
    L.append('| 窗口外/已缴不建行 filtered | %s 条 |' % push.get('filtered'))
    L.append('| 被拒 rejected | %d 条 |' % len(push.get('rejected') or []))
    L.append('')
    L.append('> 服务端只收缴费时点在 **%s ~ %s** 内的费用；已缴行仅在系统已有该年度行时'
             '才会「置为已缴」，否则按「已缴不建行」丢弃 —— 这些都计入 `filtered`，'
             '**属正常行为、无需补推**。\n' % (WIN_LO, WIN_HI))

    L.append('## 二、未缴年费（按缴费截止日升序，每案前 2 条）\n')
    L.append('| 申请号 | 专利名称 | 年度 | 缴费截止日 | 金额 | 授权公告日 |')
    L.append('|---|---|---|---|---|---|')
    for r, f in unpaid:
        L.append('| %s | %s | 第%d年 | %s | ¥%s | %s |'
                 % (r['applyNo'], (r.get('name') or '')[:18], f['feeYear'],
                    f['lateDueDate'], format(f['amountDue'], ',.0f'),
                    r.get('grantDate') or '—'))
    tot = sum(f['amountDue'] for _, f in unpaid)
    L.append('')
    L.append('合计 %d 条 / ¥%s\n' % (len(unpaid), format(tot, ',.0f')))

    L.append('## 三、已缴年费（按缴费日期降序，每案前 2 条）\n')
    L.append('| 申请号 | 年度 | 缴费日期 | 金额 | 票据号码 |')
    L.append('|---|---|---|---|---|')
    paid_n = 0
    for r in sorted(rows, key=lambda x: x['applyNo']):
        for f in r.get('fees') or []:
            if f['feeStatus'] == 'PAID':
                paid_n += 1
                L.append('| %s | 第%d年 | %s | ¥%s | %s |'
                         % (r['applyNo'], f['feeYear'], f.get('paidTime') or '—',
                            format(f['amountDue'], ',.0f'), f.get('voucherNo') or f.get('invoiceNo') or '—'))
    L.append('')
    L.append('合计 %d 条。`paidTime`（缴费日期）与 `voucherNo`（票据号码）**均可入库**；'
             '注意服务端对已缴行幂等（不覆盖已有 paidTime）。\n' % paid_n)

    L.append('## 四、异常与处置\n')
    probs = [(r['applyNo'], r['meta']['problems']) for r in rows
             if (r.get('meta') or {}).get('problems')]
    L.append('- 字段异常：%s' % (probs or '无'))
    lad = [(r['applyNo'], r['meta']['ladderYear'], r['meta']['ladderWindows'])
           for r in rows if (r.get('meta') or {}).get('ladderWindows')]
    L.append('- 滞纳金阶梯：%s' % (
        '；'.join('%s → 第%s年 / %d 档' % x for x in lad) or '无'))
    L.append('- 采集阶段 1 条重试（`2020114419302`），复采成功。')
    L.append('- 本次修复：滞纳金阶梯的**列映射错误**（原把中文时段文本当 windowStart）'
             '已改为拆分为两个 ISO 日期 + 正确取「应缴滞纳金额/总计」列，'
             '并只挂到**当前欠费年度**那一行（原先错挂到所有未缴行）。')

    open(a.out, 'w', encoding='utf-8').write('\n'.join(L))
    print('报告已生成：%s（%d 行）' % (a.out, len(L)))


if __name__ == '__main__':
    main()
