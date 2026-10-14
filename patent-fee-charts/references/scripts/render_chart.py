#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行报表定义并把结果渲染成终端文本。

调用 SaaS MCP 工具：
  - `list_report_datasets`  取数据集与字段元数据（产出定义前必做）
  - `query_report`          执行报表定义（与智小勤图表走同一条报表引擎链路）

token 只从 ~/.workbuddy/mcp.json 内联读取，**不打印、不落盘、不进日志**。

用法:
    python3 render_chart.py --list                        # 列数据集与字段
    python3 render_chart.py --definition def.json         # 执行并渲染
    echo '<json>' | python3 render_chart.py --definition -
    python3 render_chart.py --definition def.json --json  # 只回原始 JSON（不渲染）

退出码:
    0 成功 · 2 配置缺失 · 3 凭证失效 · 4 工具/定义报错 · 5 网络失败
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CFG = os.path.expanduser('~/.workbuddy/mcp.json')
SERVER = 'IPBuddy-Server'
BAR_WIDTH = 20
LABEL_MAX = 12


# ---------------------------------------------------------------- MCP 客户端

def load_srv(cfg_path=CFG):
    """读取端点与 Authorization 头。绝不返回值本身。"""
    try:
        cfg = json.load(open(cfg_path, encoding='utf-8'))
        srv = cfg['mcpServers'][SERVER]
        return srv['url'], srv['headers']['Authorization']
    except Exception as e:
        print('无法读取 MCP 配置 %s（%s）\n请先在 SaaS「MCP接入凭证」生成 token 并写入该文件。'
              % (cfg_path, e), file=sys.stderr)
        sys.exit(2)


def rpc(url, auth, body, retries=4):
    """POST 到 MCP 端点；对网关瞬时故障（502/503/504）退避重试。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream',
                         'Authorization': auth},
                method='POST')
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (502, 503, 504) and i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:
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
                               "clientInfo": {"name": "render_chart", "version": "1"}}})
    raw = rpc(url, auth, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    try:
        outer = json.loads(raw)
        if 'error' in outer:
            return {'_rpc_error': outer['error']}
        return json.loads(outer['result']['content'][0]['text'])
    except Exception:
        return {'_raw': raw[:4000]}


# ---------------------------------------------------------------- 格式化

def fmt_num(v):
    """数值格式化：整数千分位，小数最多 2 位；非数值原样返回。"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return '{:,}'.format(v)
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return '{:,}'.format(int(round(v)))
        return '{:,.2f}'.format(v)
    return str(v)


def fmt_ratio(v):
    """分位数 / 派生统计量：最多 4 位小数，去尾随零。"""
    if v is None:
        return ''
    try:
        s = '%.4f' % float(v)
    except (TypeError, ValueError):
        return str(v)
    return s.rstrip('0').rstrip('.') if '.' in s else s


def clip(label):
    """类别名过长时截断（终端一行放不下会折行，反而看不清）。"""
    s = '' if label is None else str(label)
    return s if len(s) <= LABEL_MAX else s[:LABEL_MAX - 1] + '…'


def md_table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + ' --- |' * len(headers)]
    for r in rows:
        out.append('| ' + ' | '.join('' if c is None else str(c) for c in r) + ' |')
    return '\n'.join(out)


# ---------------------------------------------------------------- 渲染

def render_metric(data):
    """metric 卡：有维度每取值一卡，无维度单卡。"""
    mv = data.get('metricView') or {}
    cards = mv.get('cards') or []
    if not cards:
        return '（引擎未返回指标卡）'
    lines = []
    for c in cards:
        primary = c.get('primary') or {}
        secondary = c.get('secondary') or []
        line = '**%s** %s' % (c.get('label'), primary.get('formatted') or fmt_num(primary.get('value')))
        if secondary:
            line += ' ｜ ' + ' · '.join(
                '%s %s' % (s.get('measureLabel'), s.get('formatted') or fmt_num(s.get('value')))
                for s in secondary)
        lines.append(line)
    return '\n'.join(lines)


def dataset_table(dataset):
    """统一聚合结果集 → markdown 表（列顺序即服务端列顺序）。"""
    cols = dataset.get('columns') or []
    rows = dataset.get('rows') or []
    if not cols:
        return '（引擎未返回列元数据）'
    dim_count = dataset.get('dimensionCount') or 0
    headers = [c.get('label') or c.get('key') for c in cols]
    body = []
    for r in rows:
        cells = []
        for i, c in enumerate(cols):
            v = r.get(c.get('key'))
            cells.append(str(v) if i < dim_count else fmt_num(v))
        body.append(cells)
    return md_table(headers, body)


def render_bars(labels, values, series_name, unit_hint=''):
    """字符条形图（只画第一个系列）。含负值则不画。"""
    nums = [v for v in values if v is not None]
    if not nums or min(nums) < 0:
        return ''
    mx = max(nums) or 0
    if mx <= 0:
        return ''
    per = mx / float(BAR_WIDTH)
    lines = ['%s（每格 ≈ %s%s）' % (series_name, fmt_num(per), unit_hint)]
    for lb, v in zip(labels, values):
        n = 0 if not v else max(1, int(round(v / per)))
        lines.append('%-14s %s  %s' % (clip(lb), '█' * min(n, BAR_WIDTH), fmt_num(v)))
    return '\n'.join(lines)


def render_chart(data):
    view = data.get('view') or {}
    dataset = data.get('dataset') or {}
    parts = []

    # 单值兜底
    if view.get('singleValue') is not None and not view.get('categories'):
        return '**%s** %s' % (view.get('singleLabel') or '单值', fmt_num(view.get('singleValue')))

    # 热力图：矩阵表
    matrix = view.get('matrix')
    if matrix:
        x, y = matrix.get('xLabels') or [], matrix.get('yLabels') or []
        grid = [[0.0] * len(x) for _ in y]
        for p in (matrix.get('points') or []):
            try:
                grid[int(p[1])][int(p[0])] = p[2]
            except (IndexError, TypeError, ValueError):
                pass
        rows = [[y[i]] + [fmt_num(grid[i][j]) for j in range(len(x))] for i in range(len(y))]
        parts.append(md_table([''] + x, rows))
        parts.append('（热力图已降级为矩阵表；色泽语义在终端不可用，数值见单元格）')
        return '\n\n'.join(parts)

    # 层级树
    if view.get('hierarchy'):
        def walk(nodes, depth):
            out = []
            for n in nodes:
                out.append('%s- %s (%s)' % ('  ' * depth, n.get('name'), fmt_num(n.get('value'))))
                if n.get('children'):
                    out.extend(walk(n['children'], depth + 1))
            return out
        return '\n'.join(walk(view['hierarchy'], 0))

    # 箱线 / K 线：只出统计量表
    cats = view.get('categories') or []
    if view.get('boxData'):
        rows = [[cats[i] if i < len(cats) else i] + [fmt_ratio(v) for v in row]
                for i, row in enumerate(view['boxData'])]
        parts.append(md_table(['类别', '最小', 'Q1', '中位', 'Q3', '最大'], rows))
        parts.append('（箱线图已降级为统计量表）')
        return '\n\n'.join(parts)
    if view.get('candleData'):
        rows = [[cats[i] if i < len(cats) else i] + [fmt_ratio(v) for v in row]
                for i, row in enumerate(view['candleData'])]
        parts.append(md_table(['类别', '开盘', '收盘', '最低', '最高'], rows))
        parts.append('（K 线图已降级为四价表）')
        return '\n\n'.join(parts)

    # 常规分类图：表格 + 首系列字符条形图
    series = view.get('series') or []
    if not cats and not series:
        return dataset_table(dataset) if dataset.get('columns') else '（引擎未返回图表模型）'

    if series:
        headers = [view.get('categoryLabel') or '类别'] + [s.get('name') for s in series]
        rows = []
        for i, c in enumerate(cats):
            rows.append([c] + [fmt_num(s['data'][i] if i < len(s.get('data') or []) else None)
                               for s in series])
        parts.append(md_table(headers, rows))
        first = series[0]
        bars = render_bars(cats, first.get('data') or [], first.get('name') or '数值')
        if bars:
            parts.append(bars)
        else:
            parts.append('（含负值或全零，终端不出条形图，数值见表）')
        if view.get('isSplit') and len(series) > 1:
            parts.append('（拆分维度产生的多系列由表格承载，终端不并排画柱）')
        return '\n\n'.join(parts)

    parts.append('| %s | 数值 |' % (view.get('categoryLabel') or '类别'))
    parts.append('| --- | --- |')
    for c in cats:
        parts.append('| %s |  |' % c)
    return '\n\n'.join(parts)


def render(payload):
    """把 ReportQueryRespDTO.data 渲染成终端文本。"""
    dtype = payload.get('type')
    dataset = payload.get('dataset') or {}
    head = ''
    if dataset.get('truncated'):
        head = '> 结果已截断（非全量），不得据此做合计或家数陈述。\n\n'
    if dtype == 'metric':
        body = render_metric(payload)
    elif dtype == 'table':
        body = dataset_table(dataset)
        if dataset.get('truncated'):
            body += '\n\n（结果已截断，非全量）'
    else:
        body = render_chart(payload)
    return head + body


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--definition', help='报表定义 JSON 文件路径，或 - 表示从 stdin 读')
    ap.add_argument('--list', action='store_true', help='列出可查数据集与字段后退出')
    ap.add_argument('--json', action='store_true', help='打印工具原始 JSON，不做渲染')
    ap.add_argument('--config', default=CFG, help='mcp.json 路径（默认 ~/.workbuddy/mcp.json）')
    a = ap.parse_args()

    if not a.list and not a.definition:
        ap.error('需要 --list 或 --definition')

    url, auth = load_srv(a.config)

    # Step 0：凭证预检（失败即停）
    try:
        token = call_tool(url, auth, 'check_token', {})
    except Exception as e:
        print('调用 MCP 失败：%s' % e, file=sys.stderr)
        return 5
    if not token.get('valid'):
        print('凭证无效（valid=false）：请到 SaaS「MCP接入凭证」重新生成 token 并更新 %s'
              % a.config, file=sys.stderr)
        return 3

    # --list：数据集与字段（产出定义前必做）
    if a.list:
        try:
            metas = call_tool(url, auth, 'list_report_datasets', {})
        except Exception as e:
            print('调用 list_report_datasets 失败：%s' % e, file=sys.stderr)
            return 5
        if a.json:
            print(json.dumps(metas, ensure_ascii=False, indent=2))
            return 0
        for m in (metas or []):
            print('## %s（%s）' % (m.get('name'), m.get('id')))
            if m.get('description'):
                print(m['description'])
            if m.get('defaultDimensions') or m.get('defaultChartType'):
                print('默认视图：%s %s' % (m.get('defaultChartType'),
                                      ' / '.join(m.get('defaultDimensions') or [])))
            rows = []
            for f in (m.get('fields') or []):
                rows.append([f.get('name'), f.get('label'), f.get('dataType'), f.get('role'),
                             ','.join(f.get('aggregations') or []) or '-',
                             'Y' if f.get('filterable') else '',
                             'Y' if f.get('currencyBound') else ''])
            print(md_table(['字段', '展示名', '类型', '角色', '可用聚合', '可筛', '币种绑定'], rows))
            print()
        return 0

    # 读定义
    try:
        if a.definition == '-':
            definition = json.load(sys.stdin)
        else:
            definition = json.load(open(a.definition, encoding='utf-8'))
    except Exception as e:
        print('报表定义无法解析：%s' % e, file=sys.stderr)
        return 4

    # Step 3：执行
    try:
        result = call_tool(url, auth, 'query_report', {'request': definition})
    except Exception as e:
        print('调用 query_report 失败：%s' % e, file=sys.stderr)
        return 5
    if '_rpc_error' in result or '_raw' in result:
        print('工具调用未返回可解析结果：%s' % json.dumps(result, ensure_ascii=False)[:1000],
              file=sys.stderr)
        return 4

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # 引擎的"可预期失败"不是异常：照 message 如实告知，不重试刷屏
    if not result.get('success'):
        err = result.get('error') or {}
        print('查询未成功：%s —— %s' % (err.get('code'), err.get('message')), file=sys.stderr)
        return 4

    payload = result.get('data') or {}
    print(render(payload))

    # 收尾提示（走 stderr，不污染正文）
    ds = payload.get('dataset') or {}
    print('\n（数据集：%s；类型：%s%s）'
          % ((definition.get('datasetId') or ''), payload.get('type'),
             '；结果已截断' if ds.get('truncated') else ''), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
