import {session} from '../stores/session.js';
import {replicationsAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {SHARD_NAMES,RECLAIM_REASONS,ADVANCE_REASONS} from '../constants/enums.js';
import {esc} from '../utils/html.js';
import {message} from '../hooks/action.js';

const writesText=writes=>writes.map(w=>`${esc(w.key)}=${w.value}@${w.lsn}`).join('<br>');

const ledgerRows=(ledger,expected)=>Object.keys(ledger||{}).map(k=>`<tr><td>${esc(k)}</td><td>${ledger[k]}</td><td>${expected[k]}</td><td>${ledger[k]===expected[k]?'一致':'<span class="mismatch">不一致</span>'}</td></tr>`).join('');

function shardCard(rep,shard){
 const r=rep.replicas[shard];
 const check=rep.checks[shard];
 const canWrite=session.role!=='viewer';
 const pending=(r.pending||[]).slice(0,4).map(t=>`${t.tx}(LSN ${t.begin_lsn}–${t.commit_lsn})`).join('、')||'无';
 const next=r.next_tx?`${r.next_tx.tx} · 提交于 LSN ${r.next_tx.commit_lsn}`:'已追平';
 const unavailable=r.unavailable||[];
 const caughtUp=r.applied_count===rep.committed_count;
 const resumable=!unavailable.length;
 return `<section class="shard-card" data-shard="${shard}"><div class="shard-head"><h3>${SHARD_NAMES[shard]}</h3><span class="badge ${caughtUp?'completed':'queued'}">${caughtUp?'与源库一致':`落后 ${r.lag} 个事务`}</span></div>
 <dl class="shard-meta"><div><dt>进度</dt><dd>${r.applied_count} / ${rep.committed_count} 个提交事务</dd></div><div><dt>下一笔</dt><dd>${esc(next)}</dd></div><div><dt>待入账</dt><dd>${esc(pending)}</dd></div><div><dt>账本摘要</dt><dd class="mono">${esc((r.ledger_sha256||'').slice(0,16))}…</dd></div><div><dt>末条回执</dt><dd>${r.last_receipt?`${esc(r.last_receipt.tx)} · 进度 ${r.last_receipt.applied_count}${r.last_receipt.duplicate?' · 重复确认':''}`:'无'}</dd></div><div><dt>可续接</dt><dd>${resumable?'日志完整':'<span class="mismatch">日志被回收</span>'}</dd></div></dl>
 ${unavailable.length?`<p class="notice error">以下事务的日志已被回收，无法续接：${esc(unavailable.map(t=>t.tx).join('、'))}</p>`:''}
 <div class="table-wrap"><table><thead><tr><th>账本键</th><th>分库账本</th><th>按进度应有</th><th>核对</th></tr></thead><tbody>${ledgerRows(r.ledger,check.expected_ledger)}</tbody></table></div>
 <p class="checkline">进度与账本一致：<b>${check.ledger_matches_progress?'是':'否'}</b> · 提交序重放 = LSN 序重放：<b>${check.commit_order_matches_lsn_order?'是':'否'}</b> · 账本摘要校验：<b>${check.ledger_sha256_matches?'通过':'失败'}</b></p>
 ${canWrite?`<div class="toolbar"><button data-act="advance-all" data-shard="${shard}"${caughtUp?' disabled':''}>整笔追平</button><button class="secondary" data-act="advance-one" data-shard="${shard}"${caughtUp?' disabled':''}>只入账一笔</button><button class="secondary" data-act="loss" data-shard="${shard}"${caughtUp?' disabled':''}>模拟回执丢失</button>${r.last_receipt?`<button class="secondary" data-retry="${esc(r.last_receipt.tx)}" data-shard="${shard}">重试末条回执</button>`:''}</div>`:'<p class="readonly">观察员仅可核对账本与进度。</p>'}
 </section>`;
}

function planSection(rep){
 const committed=rep.plan.committed.map(t=>[t.seq,esc(t.tx),t.begin_lsn,t.commit_lsn,t.write_count,writesText(t.writes),t.begin_lsn>rep.truncated_lsn?'保留':'已回收']);
 const excluded=rep.plan.excluded.map(t=>[esc(t.tx),t.begin_lsn,t.end_lsn,t.reason==='aborted_before_crash'?'断电点前已回滚':'断电点时未提交',t.commit_after_crash_lsn?`断电点后 LSN ${t.commit_after_crash_lsn} 才提交，不入账`:(t.abort_after_crash_lsn?`断电点后 LSN ${t.abort_after_crash_lsn} 回滚`:'—')]);
 return `<h2>同步计划（按提交顺序整笔入账）</h2>${table(['#','事务','begin LSN','commit LSN','写次数','写入','日志'],committed)}${excluded.length?`<details><summary>被排除的事务（${excluded.length}）</summary>${table(['事务','begin LSN','end LSN','原因','说明'],excluded)}</details>`:''}`;
}

function reclaimSection(rep){
 const retired=rep.plan.crash_after-rep.retained_records.length;
 return `<h2>日志回收</h2><p>回收只会裁掉两处分库都用不到的前缀，并停在未入账事务的 begin 之前；跨过裁切位置的事务保留完整记录，慢分库仍可从原进度续接。</p>
 <dl class="shard-meta"><div><dt>裁切位置</dt><dd>LSN ${rep.truncated_lsn}</dd></div><div><dt>断电点</dt><dd>LSN ${rep.plan.crash_after}</dd></div><div><dt>已回收</dt><dd>${retired} 条</dd></div><div><dt>仍保留</dt><dd>${rep.retained_records.length} 条</dd></div></dl>
 <div class="table-wrap"><table><thead><tr><th>LSN</th><th>操作</th><th>事务</th><th>键</th><th>值</th><th>日志状态</th></tr></thead><tbody>${rep.retained_records.length?rep.retained_records.map(r=>`<tr><td>${r.lsn}</td><td>${esc(r.op)}</td><td>${esc(r.tx||'—')}</td><td>${esc(r.key||'—')}</td><td>${r.value??'—'}</td><td>保留</td></tr>`).join(''):'<tr><td colspan="6">断电点前缀已全部回收。</td></tr>'}</tbody></table></div>
 ${rep.reclaims.length?`<details><summary>回收历史（${rep.reclaims.length} 次）</summary>${table(['时间','裁切位置','原因','受保护事务'],rep.reclaims.map(x=>[esc(x.created_at||'—'),`${x.previous_lsn} → ${x.truncated_lsn}`,esc(RECLAIM_REASONS[x.reason]||x.reason),esc((x.protected||[]).map(t=>t.tx).join('、')||'无')]))}</details>`:''}`;
}

export function shardPage(rep){
 const canWrite=session.role!=='viewer';
 return `<div class="section-title"><h2>${esc(rep.label)} <small>#${rep.id}</small></h2><span>同步计划指纹 <span class="mono">${esc(rep.plan.fingerprint.slice(0,12))}…</span></span></div>
 <section class="verdict"><div><p>追账状态</p><h2>${rep.all_caught_up?'两处均已追平':(rep.shards_in_step?'两处同步推进':'两处进度不同')}</h2></div><p>可同步事务 ${rep.committed_count} 笔（断电点 ${rep.plan.crash_after} 条日志）<br>裁切位置 LSN ${rep.truncated_lsn} · 已回收 ${rep.plan.crash_after-rep.retained_records.length} 条<br>分库 A ${rep.replicas.a.applied_count}/${rep.committed_count} · 分库 B ${rep.replicas.b.applied_count}/${rep.committed_count}</p></section>
 <div class="shard-grid">${shardCard(rep,'a')}${shardCard(rep,'b')}</div>
 <div class="toolbar"><button id="reclaim-preview" class="secondary">预览可回收位置</button>${canWrite?'<button id="reclaim">按安全位置回收</button>':''}<button id="refresh" class="secondary">刷新账本与进度</button></div>
 <div id="reclaim-result"></div>
 ${planSection(rep)}
 ${reclaimSection(rep)}
 <details><summary>查看冻结输入与完整计划</summary><pre>${esc(JSON.stringify({snapshot:rep.snapshot,plan:rep.plan},null,2))}</pre></details>`;
}

export function bindShard(rep,render){
 const id=rep.id;
 const run=async(fn)=>{try{await fn();}catch(err){message(err.message,true);}};
 document.querySelectorAll('[data-act]').forEach(btn=>btn.onclick=()=>run(async()=>{
  btn.disabled=true;
  try{
   const shard=btn.dataset.shard;
   let note='';
   if(btn.dataset.act==='advance-all'){
    const out=await replicationsAPI.advance(id,shard,{limit:0});
    note=out.reason==='up_to_date'?'已追平，无需入账。':`已按提交顺序整笔入账，进度 ${out.to_count} / ${out.to_count+out.lag}。`;
   }
   if(btn.dataset.act==='advance-one'){
    const out=await replicationsAPI.advance(id,shard,{limit:1});
    note=out.reason==='up_to_date'?'已追平，无需入账。':`已入账 ${out.applied_txs.map(t=>t.tx).join('、')||'一笔'}，进度 ${out.to_count}。`;
   }
   if(btn.dataset.act==='loss'){
    const out=await replicationsAPI.advance(id,shard,{simulate_receipt_loss:true});
    note=`回执丢失：${out.lost_receipt?.tx||'一笔'} 已入账但未获确认。请点“重试末条回执”核对不会重复入账。`;
   }
   await render();
   message(note,btn.dataset.act==='loss');
  }finally{btn.disabled=false;}
 }));
 document.querySelectorAll('[data-retry]').forEach(el=>el.onclick=()=>run(async()=>{
  const out=await replicationsAPI.advance(id,el.dataset.shard,{receipt:el.dataset.retry});
  await render();
  message(out.duplicate?`${ADVANCE_REASONS.duplicate_receipt}：仍为 ${out.to_count}。`:`${ADVANCE_REASONS[out.reason]||out.reason}：进度 ${out.to_count}。`,!out.duplicate);
 }));
 document.querySelector('#refresh').onclick=()=>run(async()=>{await render();message('已刷新账本与进度。');});
 document.querySelector('#reclaim-preview').onclick=()=>run(async()=>{
  const out=await replicationsAPI.reclaim(id,{dry_run:true});
  const box=document.querySelector('#reclaim-result');
  box.innerHTML=`<div class="notice">${esc(RECLAIM_REASONS[out.reclaim.reason]||out.reclaim.reason)}：当前位置 LSN ${out.reclaim.previous_lsn} → 可安全回收到 LSN ${out.reclaim.candidate_lsn}。受保护事务：${esc((out.reclaim.protected||[]).map(t=>t.tx).join('、')||'无')}。${out.resume_safe?'两处分库均可不丢事务地续接。':'有分库无法续接，请先核对。'}</div>`;
 });
 document.querySelector('#reclaim')?.addEventListener('click',()=>run(async()=>{
  const out=await replicationsAPI.reclaim(id,{});
  const note=out.reclaim.recorded?`已回收到 LSN ${out.truncated_lsn}，保留 ${out.retained_records.length} 条日志。`:`未再回收：${RECLAIM_REASONS[out.reclaim.reason]||out.reclaim.reason}`;
  await render();
  message(note);
 }));
}
