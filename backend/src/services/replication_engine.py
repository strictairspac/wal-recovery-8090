"""两个下游分库从同一段采样日志追账的教学模型。

规则：
- 同步源只包含断电点（crash_after）之前已经 commit 的事务；
- 事务按 commit 顺序整笔入账，未提交/已回滚事务不入账；
- 每个分库的进度是“已入账事务数”，账本总是该进度对应的提交前缀；
- 回收只能裁掉事务边界之前的日志，跨过裁切位置且仍待分库入账的事务会被保护；
- 所有函数无副作用，便于独立测试和前端展示。
"""
import copy
import hashlib
import json

from src.services.wal_engine import validate


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan_fingerprint(initial, records, crash_after):
    return _digest({'initial': initial, 'records': records, 'crash_after': crash_after})


def ledger_digest(ledger):
    return _digest(ledger)


def build_plan(initial, records, crash_after):
    """把断电点前的日志整理成“按提交顺序整笔入账”的同步计划。

    committed 已按 commit LSN 升序排列；excluded 列出被排除的事务（断电点前
    abort，或断电点时仍未提交），transactions 汇总前缀内所有事务边界，供回收
    日志时保护跨过裁切位置的事务。
    """
    validate(initial, records)
    if not isinstance(crash_after, int) or isinstance(crash_after, bool):
        raise ValueError('崩溃点必须为整数')
    if not 0 <= crash_after <= len(records):
        raise ValueError('崩溃点超出日志范围')

    prefix = records[:crash_after]
    active = {}
    committed = []
    excluded = []
    transactions = []

    for lsn, row in enumerate(prefix, 1):
        op = row['op']
        tx = row.get('tx')
        if op == 'begin':
            active[tx] = {'tx': tx, 'begin_lsn': lsn, 'writes': []}
        elif op == 'write':
            active[tx]['writes'].append({'lsn': lsn, 'key': row['key'], 'value': row['value']})
        elif op == 'commit':
            item = active.pop(tx)
            entry = {
                'seq': len(committed) + 1,
                'tx': tx,
                'begin_lsn': item['begin_lsn'],
                'commit_lsn': lsn,
                'write_count': len(item['writes']),
                'writes': item['writes'],
            }
            committed.append(entry)
            transactions.append({
                'tx': tx, 'status': 'committed', 'seq': entry['seq'],
                'begin_lsn': item['begin_lsn'], 'end_lsn': lsn,
            })
        elif op == 'abort':
            item = active.pop(tx)
            excluded.append({
                'tx': tx,
                'reason': 'aborted_before_crash',
                'begin_lsn': item['begin_lsn'],
                'end_lsn': lsn,
                'writes': item['writes'],
            })
            transactions.append({
                'tx': tx, 'status': 'aborted', 'seq': None,
                'begin_lsn': item['begin_lsn'], 'end_lsn': lsn,
            })
        # checkpoint 不改变事务集合

    post_commits = {}
    post_aborts = {}
    for lsn, row in enumerate(records[crash_after:], crash_after + 1):
        if row['op'] == 'commit':
            post_commits[row['tx']] = lsn
        elif row['op'] == 'abort':
            post_aborts[row['tx']] = lsn

    # 断电点时仍未提交的事务：断电点之后即使 commit，也不进入同步计划。
    for tx, item in active.items():
        end_lsn = item['writes'][-1]['lsn'] if item['writes'] else item['begin_lsn']
        excluded.append({
            'tx': tx,
            'reason': 'active_at_crash',
            'begin_lsn': item['begin_lsn'],
            'end_lsn': end_lsn,
            'writes': item['writes'],
            'commit_after_crash_lsn': post_commits.get(tx),
            'abort_after_crash_lsn': post_aborts.get(tx),
        })
        transactions.append({
            'tx': tx, 'status': 'active_at_crash', 'seq': None,
            'begin_lsn': item['begin_lsn'], 'end_lsn': end_lsn,
        })

    # 断电点之后才 begin 的事务同样不属于本次同步源，单独列出便于核对。
    suffix_tx = {}
    for lsn, row in enumerate(records[crash_after:], crash_after + 1):
        op = row['op']
        tx = row.get('tx')
        if op == 'begin':
            suffix_tx[tx] = {'tx': tx, 'begin_lsn': lsn, 'commit_lsn': None, 'abort_lsn': None}
        elif op in ('commit', 'abort') and tx in suffix_tx:
            suffix_tx[tx]['commit_lsn' if op == 'commit' else 'abort_lsn'] = lsn

    excluded.sort(key=lambda item: item['begin_lsn'])
    transactions.sort(key=lambda item: item['begin_lsn'])
    return {
        'crash_after': crash_after,
        'prefix_records': crash_after,
        'suffix_records': len(records) - crash_after,
        'committed': committed,
        'excluded': excluded,
        'transactions': transactions,
        'after_crash_transactions': list(suffix_tx.values()),
        'fingerprint': plan_fingerprint(initial, records, crash_after),
        'committed_count': len(committed),
    }


def expected_ledger(initial, committed, applied):
    """按提交顺序把前 applied 个事务整笔写入，得到应有账本。"""
    if isinstance(applied, bool) or not isinstance(applied, int):
        raise ValueError('进度必须为整数')
    if applied < 0 or applied > len(committed):
        raise ValueError('进度超出同步计划范围')
    ledger = copy.deepcopy(initial)
    for tx in committed[:applied]:
        for write in tx['writes']:
            ledger[write['key']] = write['value']
    return ledger


def lsn_order_ledger(initial, committed, applied):
    """独立核对：把同样的事务写入按全局 LSN 重放。

    严格写锁保证同一键的写入顺序与提交顺序一致，因此结果必须与整笔入账相同。
    """
    if applied < 0 or applied > len(committed):
        raise ValueError('进度超出同步计划范围')
    ledger = copy.deepcopy(initial)
    writes = [write for tx in committed[:applied] for write in tx['writes']]
    for write in sorted(writes, key=lambda item: item['lsn']):
        ledger[write['key']] = write['value']
    return ledger


def _tx_brief(item):
    if item is None:
        return None
    return {
        'seq': item.get('seq'),
        'tx': item['tx'],
        'begin_lsn': item['begin_lsn'],
        'commit_lsn': item['commit_lsn'],
        'write_count': item['write_count'],
    }


def compute_cut(plan, applied_counts, truncated_lsn):
    """计算安全回收位置。

    只回收所有分库都用不到的前缀：
    - 仍有分库没有入账的已提交事务，必须在它的 begin 之前停住；
    - 裁切位置不能落在任何事务内部（跨过裁切位置的事务保留完整记录）；
    - 全部追平后，整个断电点前缀都可以回收。
    返回的位置只会前进，不会回头。
    """
    committed = plan['committed']
    crash_after = plan['crash_after']
    if not applied_counts:
        return {
            'previous_lsn': truncated_lsn,
            'truncated_lsn': truncated_lsn,
            'candidate_lsn': truncated_lsn,
            'changed': False,
            'reason': 'no_replicas',
            'protected': [],
            'boundary_tx': None,
            'crash_after': crash_after,
            'applied_counts': [],
        }
    for count in applied_counts:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > len(committed):
            raise ValueError('分库进度超出同步计划范围')

    min_applied = min(applied_counts)
    needed = committed[min_applied:]
    min_applied_lsn = committed[min_applied - 1]['commit_lsn'] if min_applied else 0

    if needed:
        boundary = min(needed, key=lambda item: item['begin_lsn'])
        candidate = boundary['begin_lsn'] - 1
    else:
        boundary = None
        candidate = crash_after

    candidate = min(candidate, crash_after)

    # 任何事务都不能被裁成两半：若候选位置落在某事务内部，退到它的 begin 之前。
    straddlers = []
    moved = True
    while moved:
        moved = False
        for item in plan.get('transactions', []):
            if item['begin_lsn'] <= candidate < item['end_lsn']:
                if not any(s['tx'] == item['tx'] for s in straddlers):
                    straddlers.append({
                        'tx': item['tx'], 'begin_lsn': item['begin_lsn'], 'end_lsn': item['end_lsn'],
                    })
                candidate = item['begin_lsn'] - 1
                moved = True
    candidate = max(0, candidate)

    new_cut = max(truncated_lsn, candidate)
    if new_cut > truncated_lsn:
        reason = 'prefix_consumed' if not needed else 'transaction_boundary'
    elif needed:
        reason = 'slowest_replica_pending'
    elif truncated_lsn >= crash_after:
        reason = 'fully_reclaimed'
    else:
        reason = 'already_reclaimed'

    return {
        'previous_lsn': truncated_lsn,
        'truncated_lsn': new_cut,
        'candidate_lsn': candidate,
        'changed': new_cut > truncated_lsn,
        'reason': reason,
        'min_applied_count': min_applied,
        'min_applied_commit_lsn': min_applied_lsn,
        'boundary_tx': _tx_brief(boundary),
        'protected': [_tx_brief(item) for item in needed],
        'straddled_protected': straddlers,
        'applied_counts': list(applied_counts),
        'crash_after': crash_after,
    }


def remaining_records(records, truncated_lsn, crash_after):
    """回收后仍保留在同步源前缀里的日志（带原始 LSN）。"""
    out = []
    for lsn, row in enumerate(records[:crash_after], 1):
        if lsn > truncated_lsn:
            out.append(dict(row, lsn=lsn))
    return out


def availability(committed, applied, truncated_lsn):
    """检查未入账事务的日志是否仍完整保留，可供慢分库续接。"""
    pending = committed[applied:]
    unavailable = [item for item in pending if item['begin_lsn'] <= truncated_lsn]
    return {
        'pending': [_tx_brief(item) for item in pending],
        'next_tx': _tx_brief(pending[0]) if pending else None,
        'unavailable': [_tx_brief(item) for item in unavailable],
        'resume_safe': not unavailable,
        'lag': len(pending),
    }
