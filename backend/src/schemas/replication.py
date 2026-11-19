from typing import Literal
from pydantic import BaseModel, Field

SHARDS = ('a', 'b')


class ReplicationInput(BaseModel):
    crash_case_id: int = Field(gt=0)
    label: str = Field(min_length=2, max_length=120)


class ShardInput(BaseModel):
    shard: Literal['a', 'b']
    delayed: bool = False


class AdvanceInput(BaseModel):
    """推进分库：默认整笔追平，limit>0 时最多再入账 limit 个事务。"""
    limit: int = Field(default=0, ge=0, le=200)
    simulate_receipt_loss: bool = False
    receipt: str | None = Field(default=None, max_length=120)


class ReclaimInput(BaseModel):
    """回收日志前缀。dry_run 只计算安全位置，不落盘。"""
    dry_run: bool = False
