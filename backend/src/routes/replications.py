from fastapi import APIRouter, Depends, HTTPException

from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.replication import ReplicationInput, AdvanceInput, ReclaimInput
from src.services.replication_service import (
    list_replications, detail_replication, create_replication, advance_shard, reclaim,
)
from src.services.replication_engine import remaining_records
from src.models.entities import Replication
from src.controllers.actions import audit

router = APIRouter()


@router.get('/replications')
def listing(db=Depends(get_db), user=Depends(current_user)):
    return list_replications(db)


@router.post('/replications', status_code=201)
def create(data: ReplicationInput, db=Depends(get_db), user=Depends(writable)):
    try:
        obj = create_replication(db, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    audit(db, user, f'replication:create:{obj.id}')
    db.commit()
    return detail_replication(db, obj.id)


@router.get('/replications/{ident}')
def detail(ident: int, db=Depends(get_db), user=Depends(current_user)):
    return detail_replication(db, ident)


@router.post('/replications/{ident}/shards/{shard}/advance')
def advance(ident: int, shard: str, data: AdvanceInput | None = None, db=Depends(get_db), user=Depends(writable)):
    data = data or AdvanceInput()
    try:
        result = advance_shard(
            db, ident, shard,
            limit=data.limit,
            simulate_receipt_loss=data.simulate_receipt_loss,
            replay_tx=data.receipt,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    _audit_advance(db, user, ident, shard, data, result)
    db.commit()
    return result


def _audit_advance(db, user, ident, shard, data, result):
    if result.get('reason') == 'duplicate_receipt':
        prefix = 'replication:receipt_duplicate_ignored'
        detail = f'{result.get("from_count")}->{result.get("to_count")}'
    elif data.receipt:
        prefix = 'replication:receipt_retry'
        detail = f'{result.get("from_count")}->{result.get("to_count")}'
    elif result.get('reason') == 'receipt_lost':
        prefix = 'replication:receipt_lost'
        detail = str(result.get('lost_receipt', {}).get('tx'))
    else:
        prefix = 'replication:advance'
        detail = f'{result.get("from_count")}->{result.get("to_count")}'
    audit(db, user, f'{prefix}:{ident}:{shard}:{detail}')


@router.post('/replications/{ident}/reclaim')
def reclaim_logs(ident: int, data: ReclaimInput | None = None, db=Depends(get_db), user=Depends(writable)):
    data = data or ReclaimInput()
    try:
        result = reclaim(db, ident, dry_run=data.dry_run)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not data.dry_run:
        audit(db, user, f'replication:reclaim:{ident}:{result["truncated_lsn"]}')
        db.commit()
    return result


@router.get('/replications/{ident}/retained')
def retained(ident: int, db=Depends(get_db), user=Depends(current_user)):
    """核对实际保留的日志前缀：按原始 LSN 列出未回收记录。"""
    obj = db.get(Replication, ident)
    if obj is None:
        raise HTTPException(404, '追账记录不存在')
    rows = remaining_records(obj.snapshot['log_set']['records'], obj.truncated_lsn, obj.plan['crash_after'])
    return {
        'replication_id': obj.id,
        'truncated_lsn': obj.truncated_lsn,
        'crash_after': obj.plan['crash_after'],
        'records': rows,
    }
