#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把采集结果直推 SaaS MCP `push_fee_batch`。

为什么不让 agent 直接调 MCP 工具：工具调用层对**数组参数**的序列化有缺陷
（patents/fees 无论怎么写都报 `must be array`，连空数组也失败）。本脚本直连
MCP Streamable-HTTP 端点，绕开该层。

token 只从 ~/.workbuddy/mcp.json 内联读取，**不打印、不落盘、不进日志**。

用法:
    python3 push_mcp.py --dir out [--batchid fee-YYYYmmdd-HHMMSS] [--dry-run]

输出 JSON: {received, accepted, rejected, filtered}
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

CFG = os.path.expanduser('~/.workbuddy/mcp.json')
CHUNK = 10   # 服务端单批上限


def load_srv():
    cfg = json.load(open(CFG, encoding='utf-8'))
    srv = cfg['mcpServers']['IPBuddy-Server']
    return srv['url'], srv['headers']['Authorization']


def rpc(url, auth, body, retries=4):
    """POST 到 MCP 端点。对 502/503/504 等**网关瞬时故障**自动退避重试。

    实测（2026-10-10）：服务端会零星返回 502 Bad Gateway，同一请求稍后重试即成功；
    `check_token` 正常即可证明服务端本身可用，此时不应把批次标失败。
    """
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream',
                         'Authorization': auth},
                method='POST')
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (502, 503, 504) and i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:            # 连接被重置/超时等，同样退避重试
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
    if last:
        raise last
    raise RuntimeError('rpc failed')


def call_tool(url, auth, name, args):
    rpc(url, auth, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "collect_one", "version": "5"}}})
    raw = rpc(url, auth, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    try:
        outer = json.loads(raw)
        txt = outer['result']['content'][0]['text']
        return json.loads(txt)
    except Exception:
        return {'raw': raw[:2000]}


def meta_of(outdir, batchid=None):
    mp = os.path.join(outdir, 'meta.json')
    if os.path.exists(mp):
        return json.load(open(mp, encoding='utf-8'))
    now = datetime.datetime.now().astimezone()
    m = {'batchId': batchid or ('fee-' + now.strftime('%Y%m%d-%H%M%S')),
         'collectedAt': now.isoformat()}
    json.dump(m, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return m


def fee_item(f):
    """FeeItem → payload。

    **字段名与语义一律以 `push_fee_batch` 的 MCP 工具 schema（服务端 DTO）为准**，
    不以 `list_fee_ledgers` 的返回列名为准 —— 后者是平台计算引擎的**只读输出**，
    工具描述已明确「金额与状态由平台计算引擎维护，回传数据不影响清单」，
    其 `dueDate`/`payableAmount`/`lateFee` 等列由服务端按规则自行推算，与上报字段不同源。

    2026-10-13 按 DTO 全面核对后的要点：
      - `feeStatus` 用**枚举字符串**（UNPAID/DEFERRED/REDUCED/PAID），
        DTO 已于0204 起用它取代旧的 `paid` 布尔字段。
      - `paidTime`/`voucherNo` 在 DTO 里列为 **required**，但 2026-10-13 实测
        （200 条 / 5 批、零rejected）表明服务端**未强校验**未缴行的这两项。
        为稳妥起见仍对所有行显式补上空串，既满足契约形状又不改变语义。
      - 2026-10-13 起采集端也直接产出 `voucherNo`，与 DTO **同名、无需改名映射**。
        此处仍兼容改名前的旧采集结果（`invoiceNo`）与更早的 `payDate` 别名。
    """
    d = {'feeYear': f['feeYear'],
         'amountDue': f['amountDue'],
         'amountRaw': f['amountRaw'],
         'lateDueDate': f['lateDueDate'],
         'feeStatus': f['feeStatus'],
         'paidAmount': f.get('paidAmount', 0),
         'lateFeeLadder': f.get('lateFeeLadder') or [],
         'dedupKey': f['dedupKey']}
    if f.get('feeStatus') == 'PAID':
        # 票据号码：采集端已统一为 voucherNo；兼容改名前旧结果的 invoiceNo（页面列名）
        d['voucherNo'] = f.get('voucherNo') or f.get('invoiceNo') or ''
        # 缴费日期：新采集用 paidTime；兼容改名前的旧采集结果（payDate）
        d['paidTime'] = f.get('paidTime') or f.get('payDate') or ''
    else:
        # DTO 将这两项列为 required；未缴行本就没有，置空串而非省略
        d['voucherNo'] = ''
        d['paidTime'] = ''
    return d


def to_patent(res):
    """采集 JSON → payload patent。只保留契约字段，外加 grantDate（授权公告日）。"""
    return {
        'applyNo': res['applyNo'],
        'applyDate': res.get('applyDate') or '',
        'name': res.get('name') or '',
        'grantDate': res.get('grantDate') or '',
        'queryStatus': res.get('queryStatus') or 'ok',
        'failReason': res.get('failReason') or '',
        'legalStatus': res.get('legalStatus') if res.get('legalStatus') is not None else 0,
        'holderNames': res.get('holderNames') or '',
        'type': res.get('type'),
        'fees': [fee_item(f) for f in (res.get('fees') or [])],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--batchid', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.dir, '*.json')))
    files = [f for f in files if os.path.basename(f) != 'meta.json']
    if not files:
        print('目录下没有采集结果', file=sys.stderr)
        return 2

    # 只收真正的采集结果（含 applyNo 字段），跳开 debug/payload/回执文件
    res_list = []
    for f in files:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if isinstance(d, dict) and d.get('applyNo'):
            res_list.append(d)
    if not res_list:
        print('目录下没有有效的采集结果', file=sys.stderr)
        return 2

    ok = [r for r in res_list if r.get('queryStatus') == 'ok']
    bad = [r for r in res_list if r.get('queryStatus') != 'ok']

    if not ok:
        print('没有 queryStatus=ok 的专利可上报（失败项见 report）', file=sys.stderr)

    m = meta_of(a.dir, a.batchid)
    base = m['batchId']
    url, auth = load_srv()

    # ok 的按 10 条一批；无应缴的单独成批（让 SaaS 知道已授权、当前无义务）
    withfee = [r for r in ok if r.get('fees')]
    zero = [r for r in ok if not r.get('fees')]
    batches = [withfee[i:i + CHUNK] for i in range(0, len(withfee), CHUNK)]
    if zero:
        batches.append(zero)

    total = {'received': 0, 'accepted': 0, 'rejected': [], 'filtered': 0}
    out = []
    for i, b in enumerate(batches, 1):
        bid = '%s-%02d' % (base, i) if len(batches) > 1 else base
        payload = {
            'batchId': bid,
            'dataSource': 'cpquery',
            'collectedAt': m['collectedAt'],
            'total': len(b),
            'succeeded': sum(1 for x in b if x.get('queryStatus') == 'ok'),
            'failed': sum(1 for x in b if x.get('queryStatus') != 'ok'),
            'patents': [to_patent(x) for x in b],
        }
        fp = os.path.join(a.dir, '_payload_%02d.json' % i)
        json.dump(payload, open(fp, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        if a.dry_run:
            print('DRY   %-26s 专利 %2d / 年费 %3d → %s'
                  % (bid, len(b), sum(len(x.get('fees') or []) for x in b), fp))
            continue
        r = call_tool(url, auth, 'push_fee_batch', {'payload': payload})
        for k in ('received', 'accepted', 'filtered'):
            total[k] += int(r.get(k) or 0)
        total['rejected'] += (r.get('rejected') or [])
        print('PUSH  %-26s 专利 %2d / 年费 %3d → received=%s accepted=%s '
              'rejected=%d filtered=%s'
              % (bid, len(b), sum(len(x.get('fees') or []) for x in b),
                 r.get('received'), r.get('accepted'),
                 len(r.get('rejected') or []), r.get('filtered')))
        out.append({'batchId': bid, 'result': r})

    print('TOTAL received=%d accepted=%d rejected=%d filtered=%d'
          % (total['received'], total['accepted'],
             len(total['rejected']), total['filtered']))
    if bad:
        print('未上报（非 ok）：' + ' '.join(
            '%s(%s)' % (x['applyNo'], x.get('queryStatus')) for x in bad))
    json.dump({'total': total, 'batches': out},
              open(os.path.join(a.dir, 'push_result.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
