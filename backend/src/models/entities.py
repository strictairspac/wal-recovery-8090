from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(primary_key=True)
    email: Mapped[str]=mapped_column(String(120),unique=True)
    role: Mapped[str]=mapped_column(String(20))
    password_hash: Mapped[str]=mapped_column(String(250))
class LogSet(Base):
    __tablename__='log_sets'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(100),unique=True)
    initial: Mapped[dict]=mapped_column(JSON)
    records: Mapped[list]=mapped_column(JSON)
    description: Mapped[str]=mapped_column(String(500))
class CrashCase(Base):
    __tablename__='crash_cases'
    id: Mapped[int]=mapped_column(primary_key=True)
    log_set_id: Mapped[int]=mapped_column(ForeignKey('log_sets.id'))
    name: Mapped[str]=mapped_column(String(100),unique=True)
    crash_after: Mapped[int]=mapped_column(Integer)
class Recovery(Base):
    __tablename__='recoveries'
    id: Mapped[int]=mapped_column(primary_key=True)
    crash_case_id: Mapped[int]=mapped_column(ForeignKey('crash_cases.id'))
    label: Mapped[str]=mapped_column(String(120))
    status: Mapped[str]=mapped_column(String(20),default='queued')
    snapshot: Mapped[dict]=mapped_column(JSON)
    result: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Audit(Base):
    __tablename__='audits'
    id: Mapped[int]=mapped_column(primary_key=True)
    actor: Mapped[str]=mapped_column(String(120))
    action: Mapped[str]=mapped_column(String(120))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)

class Replication(Base):
    """一次双分库追账演练，冻结同步计划与回收位置；两个分库共享同一份进度。"""
    __tablename__='replications'
    id: Mapped[int]=mapped_column(primary_key=True)
    crash_case_id: Mapped[int]=mapped_column(ForeignKey('crash_cases.id'))
    label: Mapped[str]=mapped_column(String(120))
    snapshot: Mapped[dict]=mapped_column(JSON)
    plan: Mapped[dict]=mapped_column(JSON)
    truncated_lsn: Mapped[int]=mapped_column(Integer,default=0)
    reclaims: Mapped[list]=mapped_column(JSON,default=list)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)

class Replica(Base):
    """分库账本与进度、回执一起落盘；applied_count 是唯一进度真值。"""
    __tablename__='replica_shards'
    __table_args__=(UniqueConstraint('replication_id','shard',name='uq_replica_shard'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    replication_id: Mapped[int]=mapped_column(ForeignKey('replications.id'))
    shard: Mapped[str]=mapped_column(String(20))
    delayed: Mapped[bool]=mapped_column(default=False)
    applied_count: Mapped[int]=mapped_column(Integer,default=0)
    ledger: Mapped[dict]=mapped_column(JSON)
    ledger_sha256: Mapped[str]=mapped_column(String(64),default='')
    last_receipt: Mapped[dict | None]=mapped_column(JSON,nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=now)

class Receipt(Base):
    """分库入账回执。唯一约束保证回执丢失后重试不重复入账。"""
    __tablename__='replica_receipts'
    __table_args__=(UniqueConstraint('replica_id','tx',name='uq_receipt_tx'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    replication_id: Mapped[int]=mapped_column(ForeignKey('replications.id'))
    replica_id: Mapped[int]=mapped_column(ForeignKey('replica_shards.id'))
    shard: Mapped[str]=mapped_column(String(20))
    tx: Mapped[str]=mapped_column(String(30))
    seq: Mapped[int]=mapped_column(Integer)
    commit_lsn: Mapped[int]=mapped_column(Integer)
    applied_count: Mapped[int]=mapped_column(Integer)
    ledger_sha256: Mapped[str]=mapped_column(String(64))
    duplicate: Mapped[bool]=mapped_column(default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
