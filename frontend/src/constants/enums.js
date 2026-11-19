export const ROLES = {admin:'管理员',operator:'操作员',viewer:'观察员'};
export const DRILL_STATUSES = {queued:'等待执行',running:'执行中',completed:'已完成',failed:'失败'};
export const SHARD_NAMES = {a:'分库 A', b:'分库 B'};
export const RECLAIM_REASONS = {
 no_replicas:'尚无分库',
 slowest_replica_pending:'最慢分库尚有未入账事务',
 transaction_boundary:'停在未入账事务的起点之前',
 prefix_consumed:'已追平，整个断电点前缀可回收',
 already_reclaimed:'此前已回收到该位置',
 fully_reclaimed:'断电点前缀已全部回收',
};
export const ADVANCE_REASONS = {
 applied:'已按提交顺序入账',
 up_to_date:'已追平，无需入账',
 duplicate_receipt:'回执已存在，重复确认未重复入账',
 receipt_lost:'回执在返回途中丢失，需重试或刷新核对',
};
