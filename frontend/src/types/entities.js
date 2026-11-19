/** @typedef {{id:number,name:string,initial:object,records:object[],description:string}} LogSet */
/** @typedef {{id:number,name:string,log_set_id:number,crash_after:number}} CrashCase */
/** @typedef {{id:number,crash_case_id:number,label:string,status:string,snapshot:object,result:object|null}} Recovery */
/** @typedef {{applied_count:number,lag:number,ledger:object,ledger_sha256:string,last_receipt:object|null,next_tx:object|null,pending:object[],unavailable:object[],resume_safe:boolean}} ReplicaState */
/** @typedef {{id:number,crash_case_id:number,label:string,snapshot:object,plan:object,truncated_lsn:number,reclaims:object[],retained_records:object[],replicas:{a:ReplicaState,b:ReplicaState},checks:object,all_caught_up:boolean,shards_in_step:boolean}} Replication */
/** @typedef {{id:number,label:string,committed_count:number,truncated_lsn:number,crash_after:number,reclaims:number,replicas:{a:object,b:object}}} ReplicationSummary */
export {};
