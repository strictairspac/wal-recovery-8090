import {session} from './stores/session.js';
import {store} from './stores/entities.js';
import {recoveriesAPI,replicationsAPI} from './api/entities.js';
import {layout} from './components/common/layout.js';
import {loginPage,bindLogin} from './pages/login.js';
import {logsPage,bindLogs} from './pages/logs.js';
import {casesPage,bindCases} from './pages/cases.js';
import {runsPage,bindRuns} from './pages/runs.js';
import {detailPage,bindDetail} from './pages/detail.js';
import {shardsPage,bindShards} from './pages/shards.js';
import {shardPage,bindShard} from './pages/shard.js';
import {routes} from './router/routes.js';
import {esc} from './utils/html.js';

const app=document.querySelector('#app');

async function render(){
 const [key,id]=location.hash.slice(1).split('/');
 if(!session.token||key==='login'){app.innerHTML=loginPage();bindLogin();return;}
 try{
  await store.refresh();
  let body,job,rep,route=routes[key]?key:'logs';
  if(route==='logs')body=logsPage();
  if(route==='cases')body=casesPage();
  if(route==='runs')body=runsPage();
  if(route==='detail'){job=id?await recoveriesAPI.get(Number(id)):store.runs[0];body=detailPage(job);}
  if(route==='shards')body=shardsPage();
  if(route==='shard'){
   const target=Number(id||store.replications[0]?.id||0);
   if(!target){body='<section class="empty"><h2>还没有双分库追账记录</h2><p>先选择一个故障场景并冻结同步计划，页面就可以分别推进两处分库。</p><a href="#shards">进入双分库追账 →</a></section>';}
   else{rep=await replicationsAPI.get(target);body=shardPage(rep);}
  }
  app.innerHTML=layout(...routes[route],body,route==='shard'?'shards':route);
  document.querySelector('#logout').onclick=()=>{session.clear();location.hash='#login';};
  if(route==='logs')bindLogs(render);
  if(route==='cases')bindCases(render);
  if(route==='runs')bindRuns(render);
  if(route==='detail')bindDetail(job,render);
  if(route==='shards')bindShards(render);
  if(route==='shard'&&rep)bindShard(rep,render);
 }catch(e){
  app.innerHTML=layout('暂时无法载入','请检查服务状态后重试。',`<p class="notice error">${esc(e.message)}</p><button id="retry">重新加载</button>`);
  document.querySelector('#retry').onclick=render;
  document.querySelector('#logout').onclick=()=>{session.clear();location.hash='#login';};
 }
}

window.addEventListener('hashchange',render);
render();
