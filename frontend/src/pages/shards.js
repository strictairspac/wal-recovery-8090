import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {replicationsAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';

const pct=(applied,total)=>total?`${Math.round((applied/total)*100)}%`:'—';

export function shardsPage(){
 const rows=store.replications.map(r=>{
  const a=r.replicas?.a||{},b=r.replicas?.b||{};
  return [r.id,esc(r.label),r.committed_count,`${a.applied_count??0} / ${r.committed_count} · ${pct(a.applied_count,r.committed_count)}`,`${b.applied_count??0} / ${r.committed_count} · ${pct(b.applied_count,r.committed_count)}`,`${r.truncated_lsn} / ${r.crash_after}`,`<a href="#shard/${r.id}">进入追账台 →</a>`];
 });
 return `<div class="section-title"><h2>双分库追账</h2><span>快的一处不用等慢的一处</span></div>${table(['编号','追账记录','可同步事务','分库 A 进度','分库 B 进度','已回收 / 断电点','操作'],rows)}<section class="split"><div><h2>同一段日志，两处各自追</h2><p>源库只同步断电点前已提交的事务，按提交顺序整笔入账。两个分库各有账本与进度，先追平的一方不必等待另一方。</p><p>回执与进度一起落盘。回执丢失后重试，按事务标识识别重复，不会再次入账。</p><p class="big-note">慢分库从原进度续接。</p><details><summary>查看示例计划</summary><pre>${esc(JSON.stringify(store.logs[0]?.records||[],null,2))}</pre></details></div>${session.role!=='viewer'?`<form id="rep-form"><label>故障场景<select name="crash_case_id">${store.cases.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><label>记录标签<input name="label" value="晚班双分库追账" required></label><button type="submit">冻结同步计划 →</button></form>`:'<p class="readonly">观察员可查看追账记录与账本。</p>'}</section>`;
}

export function bindShards(render){action(document.querySelector('#rep-form'),async f=>{const d=formObject(f);d.crash_case_id=Number(d.crash_case_id);const rep=await replicationsAPI.create(d);location.hash='#shard/'+rep.id;},'已冻结同步计划，进入追账台。');}
