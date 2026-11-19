import {api} from './client.js';
export const logSetsAPI={list:()=>api('/log-sets'),create:data=>api('/log-sets',{method:'POST',body:JSON.stringify(data)})};
export const crashCasesAPI={list:()=>api('/crash-cases'),create:data=>api('/crash-cases',{method:'POST',body:JSON.stringify(data)})};
export const recoveriesAPI={list:()=>api('/recoveries'),get:id=>api('/recoveries/'+id),create:data=>api('/recoveries',{method:'POST',body:JSON.stringify(data)})};
export const replicationsAPI={
 list:()=>api('/replications'),
 get:id=>api('/replications/'+id),
 create:data=>api('/replications',{method:'POST',body:JSON.stringify(data)}),
 advance:(id,shard,data)=>api(`/replications/${id}/shards/${shard}/advance`,{method:'POST',body:JSON.stringify(data||{})}),
 reclaim:(id,data)=>api(`/replications/${id}/reclaim`,{method:'POST',body:JSON.stringify(data||{})}),
 retained:id=>api(`/replications/${id}/retained`),
};
