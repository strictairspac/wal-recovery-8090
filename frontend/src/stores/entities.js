import {logSetsAPI,crashCasesAPI,recoveriesAPI,replicationsAPI} from '../api/entities.js';
export const store={logs:[],cases:[],runs:[],replications:[],async refresh(){[this.logs,this.cases,this.runs,this.replications]=await Promise.all([logSetsAPI.list(),crashCasesAPI.list(),recoveriesAPI.list(),replicationsAPI.list()]);}};
