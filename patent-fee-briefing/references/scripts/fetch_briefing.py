#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取「专利年费时时报」并打印终端 markdown。

调用 SaaS MCP 工具 `get_fee_weekly_report`（只读，与管理后台年费周报邮件预览同一份数据）。

token 只从 ~/.workbuddy/mcp.json 内联读取，**不打印、不落盘、不进日志**。

用法:
    python3 fetch_briefing.py             # 打印 markdown 正文（默认）
    python3 fetch_briefing.py --json      # 打印工具返回的原始 JSON（排查/契约核对用）
    python3 fetch_briefing.py --check     # 只做凭证预检（check_token），不取数
    python3 fetch_briefing.py --out f.md  # markdown 同时写入文件（便于贴给用户）

退出码:
    0 成功 · 2 配置缺失 · 3 凭证失效 · 4 工具报错 · 5 网络失败
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
TOOL = 'get_fee_weekly_report'


def load_srv(cfg_path=CFG):
    """从 mcp.json 读取端点与 Authorization 头。绝不返回值本身。"""
    try:
        cfg = json.load(open(cfg_path, encoding='utf-8'))
        srv = cfg['mcpServers'][SERVER]
        return srv['url'], srv['headers']['Authorization']
    except Exception as e:
        print('无法读取 MCP 配置 %s（%s）\n请先在 SaaS「MCP接入凭证」生成 token 并写入该文件。'
              % (cfg_path, e), file=sys.stderr)
        sys.exit(2)


def rpc(url, auth, body, retries=4):
    """POST 到 MCP 端点。对 502/503/504 等网关瞬时故障退避重试（服务端会零星返回 502）。"""
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
        except Exception as e:                 # 连接重置 / 超时，同样退避重试
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
    if last:
        raise last
    raise RuntimeError('rpc failed')


def call_tool(url, auth, name, args):
    """initialize → tools/call，返回解析后的工具结果（dict）。"""
    rpc(url, auth, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "fetch_briefing", "version": "1"}}})
    raw = rpc(url, auth, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    try:
        outer = json.loads(raw)
        if 'error' in outer:
            return {'_rpc_error': outer['error']}
        txt = outer['result']['content'][0]['text']
        return json.loads(txt)
    except Exception:
        return {'_raw': raw[:4000]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true', help='打印原始 JSON 而不是 markdown')
    ap.add_argument('--check', action='store_true', help='只做凭证预检（check_token）')
    ap.add_argument('--out', default=None, help='markdown 同时写入该文件')
    ap.add_argument('--config', default=CFG, help='mcp.json 路径（默认 ~/.workbuddy/mcp.json）')
    a = ap.parse_args()

    url, auth = load_srv(a.config)

    # Step 0：凭证预检（失败即停，不取数）
    try:
        token = call_tool(url, auth, 'check_token', {})
    except Exception as e:
        print('调用 MCP 失败：%s' % e, file=sys.stderr)
        return 5
    if not token.get('valid'):
        print('凭证无效（valid=false）：请到 SaaS「MCP接入凭证」重新生成 token 并更新 %s'
              % a.config, file=sys.stderr)
        return 3
    if a.check:
        print('凭证有效（tenant=%s）' % token.get('tenant'))
        return 0

    # Step 1：取时时报
    try:
        data = call_tool(url, auth, TOOL, {})
    except Exception as e:
        print('调用 %s 失败：%s' % (TOOL, e), file=sys.stderr)
        return 5
    if '_rpc_error' in data or '_raw' in data:
        print('工具调用未返回可解析结果：%s' % json.dumps(data, ensure_ascii=False)[:1000],
              file=sys.stderr)
        return 4
    if not data.get('markdown'):
        print('工具返回缺少 markdown 字段：%s' % json.dumps(data, ensure_ascii=False)[:1000],
              file=sys.stderr)
        return 4

    # Step 2：原样输出（播报纪律：不改列、不重排、不美化）
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data['markdown'])
        # 截断提示由正文自带；此处只补一行确定性结论，方便 agent 转述时不漏掉
        if data.get('totalItems') and data.get('shownItems') is not None \
                and data['totalItems'] > data['shownItems']:
            print('（提示：共 %d 条待缴，终端仅呈现前 %d 条 —— 转述时请如实说明截断）'
                  % (data['totalItems'], data['shownItems']), file=sys.stderr)

    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(data['markdown'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
