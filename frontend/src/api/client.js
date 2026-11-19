import {session} from '../stores/session.js';
export async function api(path, options={}, {allowUnauthorized=false}={}) {
 const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','Authorization':'Bearer '+session.token,...options.headers}});
 if(!response.ok){const data=await response.json().catch(()=>({}));if(response.status!==401||allowUnauthorized){throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||'请求失败'));}session.clear();location.hash='#login';throw new Error('登录已失效，请重新登录');}
 return response.json();
}
export async function download(id){const r=await fetch('/api/recoveries/'+id+'/export',{headers:{Authorization:'Bearer '+session.token}});if(!r.ok)throw new Error('导出失败');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download='recovery-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
