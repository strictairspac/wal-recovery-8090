from sqlalchemy import select
from fastapi import HTTPException

from src.models.entities import Replication, Replica, Receipt, CrashCase, LogSet, now
from src.services import replication_engine as engine
from src.utils.serialize import record

SHARD_NAMES = ('a', 'b')


def _replica(db, replication_id, shard, lock=False):
    query = select(Replica).where(Replica.replication_id == replication_id, Replica.shard == shard)
    if lock:
        query = query.with_for_update()
    obj = db.scalar(query)
    if obj is None:
        raise HTTPException(404, '分库不存在')
    return obj


def _replication(db, ident, lock=False):
    query = select(Replication).where(Replication.id == ident)
    if lock:
        query = query.with_for_update()
    obj = db.scalar(query)
    if obj is None:
        raise HTTPException(404, '追账记录不存在')
    return obj


def create_replication(db, data):
    case = db.get(CrashCase, data.crash_case_id)
    if case is None:
        raise HTTPException(404, '故障场景不存在')
    logs = db.get(LogSet, case.log_set_id)
    if logs is None:
        raise HTTPException(404, '日志集不存在')
    plan = engine.build_plan(logs.initial, logs.records, case.crash_after)
    obj = Replication(
        crash_case_id=case.id,
        label=data.label,
        snapshot={'log_set': record(logs), 'case': record(case)},
        plan=plan,
        truncated_lsn=0,
        reclaims=[],
    )
    db.add(obj)
    db.flush()
    for shard in SHARD_NAMES:
        db.add(Replica(
            replication_id=obj.id,
            shard=shard,
            applied_count=0,
            ledger=dict(logs.initial),
            ledger_sha256=engine.ledger_digest(logs.initial),
            last_receipt=None,
        ))
    db.flush()
    return obj


def _shard_state(replica, committed, truncated_lsn):
    applied = replica.applied_count
    availability = engine.availability(committed, applied, truncated_lsn)
    return {
        'shard': replica.shard,
        'applied_count': applied,
        'lag': availability['lag'],
        'ledger': replica.ledger,
        'ledger_sha256': replica.ledger_sha256,
        'last_receipt': replica.last_receipt,
        'next_tx': availability['next_tx'],
        'pending': availability['pending'],
        'unavailable': availability['unavailable'],
        'resume_safe': availability['resume_safe'],
        'updated_at': replica.updated_at.isoformat() if replica.updated_at else None,
    }


def detail_replication(db, ident):
    obj = _replication(db, ident)
    committed = obj.plan['committed']
    replicas = {name: _replica(db, obj.id, name) for name in SHARD_NAMES}
    initial = obj.snapshot['log_set']['initial']
    shards = {}
    checks = {}
    for name, replica in replicas.items():
        shards[name] = _shard_state(replica, committed, obj.truncated_lsn)
        expected = engine.expected_ledger(initial, committed, replica.applied_count)
        lsn_order = engine.lsn_order_ledger(initial, committed, replica.applied_count)
        checks[name] = {
            'ledger_matches_progress': replica.ledger == expected,
            'commit_order_matches_lsn_order': expected == lsn_order,
            'ledger_sha256_matches': engine.ledger_digest(replica.ledger) == replica.ledger_sha256,
            'expected_ledger': expected,
        }
    retained = engine.remaining_records(
        obj.snapshot['log_set']['records'], obj.truncated_lsn, obj.plan['crash_after'])
    all_applied = all(shards[n]['applied_count'] == len(committed) for n in SHARD_NAMES)
    pair_consistent = shards['a']['applied_count'] == shards['b']['applied_count']
    return {
        'id': obj.id,
        'crash_case_id': obj.crash_case_id,
        'label': obj.label,
        'created_at': obj.created_at.isoformat() if obj.created_at else None,
        'snapshot': obj.snapshot,
        'plan': obj.plan,
        'committed_count': len(committed),
        'truncated_lsn': obj.truncated_lsn,
        'reclaims': obj.reclaims or [],
        'retained_records': retained,
        'reclaimed_count': obj.plan['crash_after'] - len(retained),
        'replicas': shards,
        'checks': checks,
        'all_caught_up': all_applied,
        'shards_in_step': pair_consistent,
    }


def list_replications(db):
    rows = db.scalars(select(Replication).order_by(Replication.id.desc()).limit(100)).all()
    out = []
    for obj in rows:
        shards = {}
        for name in SHARD_NAMES:
            replica = _replica(db, obj.id, name)
            shards[name] = {
                'applied_count': replica.applied_count,
                'lag': max(0, len(obj.plan['committed']) - replica.applied_count),
                'ledger_sha256': replica.ledger_sha256,
            }
        out.append({
            'id': obj.id,
            'crash_case_id': obj.crash_case_id,
            'label': obj.label,
            'committed_count': len(obj.plan['committed']),
            'truncated_lsn': obj.truncated_lsn,
            'crash_after': obj.plan['crash_after'],
            'reclaims': len(obj.reclaims or []),
            'created_at': obj.created_at.isoformat() if obj.created_at else None,
            'replicas': shards,
        })
    return out


def _receipt_brief(receipt):
    if receipt is None:
        return None
    return {
        'tx': receipt.tx,
        'seq': receipt.seq,
        'commit_lsn': receipt.commit_lsn,
        'applied_count': receipt.applied_count,
        'ledger_sha256': receipt.ledger_sha256,
        'duplicate': bool(receipt.duplicate),
        'created_at': receipt.created_at.isoformat() if receipt.created_at else None,
    }


def advance_shard(db, ident, shard, limit=0, simulate_receipt_loss=False, replay_tx=None):
    """推进单个分库，快慢互不等待。

    入账、进度、回执在同一数据库事务里落盘。simulate_receipt_loss 模拟
    “已入账但回执在返回途中丢失”，调用方重试同一事务时按回执/进度识别重复，
    账本与进度保持不变。
    """
    if shard not in SHARD_NAMES:
        raise HTTPException(422, '分库只能是 a 或 b')
    # 只锁本分库：两处分库的推进互不阻塞。同步计划与初始账本在创建后不变，
    # 裁切位置只会前进；回收按当前两处进度的最小值计算，落后的分库不会被越过。
    obj = _replication(db, ident)
    replica = _replica(db, obj.id, shard, lock=True)
    committed = obj.plan['committed']
    initial = obj.snapshot['log_set']['initial']

    def build(applied, duplicate, acknowledged, applied_txs, receipt, reason):
        expected = engine.expected_ledger(initial, committed, replica.applied_count)
        availability = engine.availability(committed, replica.applied_count, obj.truncated_lsn)
        return {
            'replication_id': obj.id,
            'shard': shard,
            'applied': bool(applied_txs),
            'duplicate': duplicate,
            'acknowledged': acknowledged,
            'reason': reason,
            'from_count': applied,
            'to_count': replica.applied_count,
            'applied_txs': applied_txs,
            'ledger': replica.ledger,
            'ledger_sha256': replica.ledger_sha256,
            'expected_ledger': expected,
            'ledger_matches_progress': replica.ledger == expected,
            'ledger_sha256_matches': engine.ledger_digest(replica.ledger) == replica.ledger_sha256,
            'last_receipt': _receipt_brief(receipt),
            'lag': availability['lag'],
            'next_tx': availability['next_tx'],
            'resume_safe': availability['resume_safe'],
            'truncated_lsn': obj.truncated_lsn,
        }

    # 回执丢失后的重试：事务已经入账，只确认重复，不再改账本和进度。
    if replay_tx is not None:
        existing = db.scalar(select(Receipt).where(
            Receipt.replica_id == replica.id, Receipt.tx == replay_tx))
        if existing is not None:
            result = build(replica.applied_count, True, True, [], existing, 'duplicate_receipt')
            result['receipt_matches_ledger'] = (
                existing.ledger_sha256 == replica.ledger_sha256
                and existing.applied_count <= replica.applied_count
            )
            return result
        matching = [item for item in committed if item['tx'] == replay_tx]
        if not matching:
            raise HTTPException(422, '事务不在本次同步计划中')
        if matching[0]['seq'] != replica.applied_count + 1:
            raise HTTPException(409, '事务尚未轮到该分库入账，请先按提交顺序推进')
        # 未入账则按正常路径继续。

    start = replica.applied_count
    if start >= len(committed):
        return build(start, False, True, [], None, 'up_to_date')

    pending = committed[start:]
    for item in pending:
        if item['begin_lsn'] <= obj.truncated_lsn:
            raise HTTPException(409, '该事务的日志已被回收，无法续接')

    if limit and limit > 0:
        target = min(start + limit, len(committed))
    else:
        target = len(committed)
    if simulate_receipt_loss:
        target = min(target, start + 1)

    applied_txs = []
    receipt = None
    from_count = replica.applied_count
    ledger = dict(replica.ledger)
    for item in committed[start:target]:
        for write in item['writes']:
            ledger[write['key']] = write['value']
        replica.applied_count = item['seq']
        replica.ledger = ledger
        replica.ledger_sha256 = engine.ledger_digest(ledger)
        receipt = Receipt(
            replication_id=obj.id,
            replica_id=replica.id,
            shard=shard,
            tx=item['tx'],
            seq=item['seq'],
            commit_lsn=item['commit_lsn'],
            applied_count=item['seq'],
            ledger_sha256=replica.ledger_sha256,
            duplicate=False,
        )
        db.add(receipt)
        db.flush()
        replica.last_receipt = _receipt_brief(receipt)
        applied_txs.append({
            'tx': item['tx'],
            'seq': item['seq'],
            'commit_lsn': item['commit_lsn'],
            'write_count': item['write_count'],
        })

    replica.updated_at = _now()
    db.flush()
    if simulate_receipt_loss:
        # 模拟“入账已发生，但确认在返回途中丢失”：响应不包含新账本、进度和回执，
        # 只告知哪一笔需要重试。调用方重试时按回执去重，账本与进度不会重复前进。
        return {
            'replication_id': obj.id,
            'shard': shard,
            'acknowledged': False,
            'reason': 'receipt_lost',
            'lost_receipt': {'tx': applied_txs[-1]['tx'] if applied_txs else None},
            'retry': {'shard': shard, 'receipt': applied_txs[-1]['tx'] if applied_txs else None},
            'simulated_receipt_loss': True,
            'truncated_lsn': obj.truncated_lsn,
        }
    return build(from_count, False, True, applied_txs, receipt, 'applied')


def reclaim(db, ident, dry_run=False):
    """回收日志前缀；只裁掉两个分库都用不到的事务边界之前。"""
    obj = _replication(db, ident, lock=True)
    replicas = {name: _replica(db, obj.id, name, lock=True) for name in SHARD_NAMES}
    committed = obj.plan['committed']
    counts = [replicas[name].applied_count for name in SHARD_NAMES]
    cut = engine.compute_cut(obj.plan, counts, obj.truncated_lsn)
    record_obj = {
        'previous_lsn': cut['previous_lsn'],
        'truncated_lsn': cut['truncated_lsn'],
        'candidate_lsn': cut['candidate_lsn'],
        'changed': cut['changed'],
        'reason': cut['reason'],
        'min_applied_count': cut.get('min_applied_count'),
        'boundary_tx': cut.get('boundary_tx'),
        'protected': cut.get('protected', []),
        'straddled_protected': cut.get('straddled_protected', []),
        'applied_counts': counts,
        'crash_after': obj.plan['crash_after'],
        'dry_run': dry_run,
    }
    if not dry_run and cut['changed']:
        obj.truncated_lsn = cut['truncated_lsn']
        obj.reclaims = list(obj.reclaims or []) + [dict(record_obj, created_at=_now().isoformat())]
        db.flush()

    shard_checks = {}
    for name in SHARD_NAMES:
        availability = engine.availability(committed, replicas[name].applied_count, cut['truncated_lsn'])
        shard_checks[name] = {
            'applied_count': replicas[name].applied_count,
            'pending': availability['pending'],
            'next_tx': availability['next_tx'],
            'unavailable': availability['unavailable'],
            'resume_safe': availability['resume_safe'],
            'ledger_sha256': replicas[name].ledger_sha256,
            'ledger_matches_progress': replicas[name].ledger == engine.expected_ledger(
                obj.snapshot['log_set']['initial'], committed, replicas[name].applied_count),
        }
    records = obj.snapshot['log_set']['records']
    retained = engine.remaining_records(records, cut['truncated_lsn'], obj.plan['crash_after'])
    return {
        'replication_id': obj.id,
        'recorded': (not dry_run) and cut['changed'],
        'truncated_lsn': obj.truncated_lsn,
        'crash_after': obj.plan['crash_after'],
        'retained_records': retained,
        'retained_count': len(retained),
        'reclaimed_count': obj.plan['crash_after'] - len(retained),
        'resume_safe': all(check['resume_safe'] for check in shard_checks.values()),
        'shards': shard_checks,
        'reclaim': record_obj,
    }


def _now():
    return now()
