#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量采集入口：逐条调用 collect_one，每条即时打印一行进度。

用法:
    python3 collect_batch.py 清单.txt [--outdir out] [--start N] [--limit M]

    --limit 默认 **10**（一批 10 条），跑完整清单用 `--limit 0`：
        collect_batch.py list.txt --start 1                # 第 1–10 条
        collect_batch.py list.txt --start 11               # 第 11–20 条
        collect_batch.py list.txt --limit 0                # 全量（一次跑完）

清单格式：每行一个申请号，# 开头为注释，空行忽略。支持 13 位原号或带小数点形式。

与 skill 红线的配合:
    - 交互场景仍建议由调用方**分批**调本脚本并逐批转述进度（不要一次静默跑完全部）。
    - 进度**逐行打印**、逐条 flush，失败条目也会打印，不静默 continue。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_one as C  # noqa: E402

DEFAULT_CHUNK = 10      # 一批默认 10 条


def read_list(path):
    nums = []
    for line in open(path, encoding='utf-8'):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        for tok in s.replace(',', ' ').split():
            n = C.norm_no(tok)
            if n and n not in nums:
                nums.append(n)
    return nums


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('listfile')
    ap.add_argument('--outdir', default='out')
    ap.add_argument('--max', type=int, default=50, help='清单总条数上限，默认 50')
    ap.add_argument('--start', type=int, default=1, help='起始序号（1-based）')
    ap.add_argument('--limit', type=int, default=DEFAULT_CHUNK,
                    help='本批采集条数（默认 %d；0=到清单末尾）' % DEFAULT_CHUNK)
    ap.add_argument('--close-when-done', action='store_true',
                    help='采集结束后关闭 Agent Window（默认保留，便于下次复用、不再弹窗）')
    a = ap.parse_args()

    nums_all = read_list(a.listfile)
    if not nums_all:
        print('清单为空', file=sys.stderr)
        return 2
    if len(nums_all) > a.max:
        print('清单 %d 条超出单批上限 %d，请拆批' % (len(nums_all), a.max),
              file=sys.stderr)
        return 2
    nums = nums_all[a.start - 1:]
    if a.limit:
        nums = nums[:a.limit]
    if not nums:
        print('切片为空（start=%d）' % a.start, file=sys.stderr)
        return 2

    if os.environ.get('SESSION'):
        session, created = os.environ['SESSION'], False
    else:
        session, created = C.ensure_session()
    tab = os.environ.get('MAIN_TAB') or (C.auto_tab(session) if session else '')
    if not session or not tab:
        print('未找到可用会话/标签页', file=sys.stderr)
        return 9
    os.makedirs(a.outdir, exist_ok=True)
    print('会话 %s · %s · 主标签 %s'
          % (session, '新建 Agent Window' if created else '复用现有窗口（不新开）', tab),
          flush=True)

    stat = {'ok': 0, 'not_found': 0, 'not_granted': 0, 'error': 0, 'login': 0}
    total_fees, total_amt = 0, 0.0
    rc2name = {0: 'ok', 1: 'not_found', 2: 'not_granted', 3: 'error', 4: 'login'}

    for i, num in enumerate(nums, 1):
        res, rc = C.collect(num, session, tab, a.outdir)
        json.dump(res, open(os.path.join(a.outdir, '%s.json' % num), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=1)
        print('[%d/%d] %s' % (a.start + i - 1, len(nums_all),
                              C.format_line(res, rc)), flush=True)
        meta = res.get('meta') or {}
        if meta.get('problems'):
            print('        ⚠ 字段异常: ' + '；'.join(meta['problems']), flush=True)
        stat[rc2name.get(rc, 'error')] += 1
        if rc == 0:
            total_fees += len(res.get('fees') or [])
            total_amt += meta.get('amount', 0)
        if rc == 4:      # 登录态失效：立即停，交用户重新登录
            print('登录态失效，停止本批。已成功 %d 条。' % stat['ok'], flush=True)
            break
        if i % 10 == 0:
            print('—— 已采 %d/%d · 累计 %d 条 / ¥%s · 失败 %d ——'
                  % (a.start + i - 1, len(nums_all), total_fees,
                     format(total_amt, ',.0f'),
                     stat['not_found'] + stat['not_granted'] + stat['error']),
                  flush=True)

    print('本批完成：成功 %d · 未查到 %d · 未授权 %d · 读取失败 %d · 登录失效 %d'
          % (stat['ok'], stat['not_found'], stat['not_granted'],
             stat['error'], stat['login']))
    print('年费合计 %d 条 / ¥%s' % (total_fees, format(total_amt, ',.0f')))

    # 默认**保留**会话/窗口：下次采集直接复用，不会再弹窗。
    # 想采完立刻清场就加 --close-when-done（会关闭该会话及其 Agent Window）。
    if a.close_when_done and not os.environ.get('SESSION'):
        C.stop_session(session)
        print('已关闭 Agent Window（会话 %s 已停止）' % session)
    return 0


if __name__ == '__main__':
    sys.exit(main())
