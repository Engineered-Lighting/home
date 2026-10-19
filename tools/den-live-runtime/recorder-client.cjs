'use strict';
// A fixed private loopback write-only recorder client; no household data in logs.
const {randomUUID}=require('node:crypto');
module.exports=function recorderClient(config){
  const url=new URL(config.service_url);
  if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.pathname!=='/'||url.search||url.hash||url.username||url.password
      ||typeof config.token!=='string'||!/^[a-f0-9]{64}$/.test(config.token))throw Error('explicit_loopback_recorder_required');
  let counter=0;const identity=randomUUID();
  return async function record(record){
    const body=JSON.stringify({id:'worker:'+identity+':'+counter,record});
    if(Buffer.byteLength(body)>1048576)throw Error('recorder_record_bound');
    // Lost response retries preserve the exact body/identity and all original clocks.
    let last;
    for(let attempt=0;attempt<2;attempt++){
      try{
        const response=await fetch(url.origin+'/record',{method:'POST',redirect:'error',headers:{Authorization:'Bearer '+config.token,
          'Content-Type':'application/json'},body,signal:AbortSignal.timeout(1500)});
        const text=await response.text();if(Buffer.byteLength(text)>4096)throw Error('recorder_response_bound');
        const value=JSON.parse(text);
        if(!response.ok||value.schema!=='home-live-recorded-1'||value.version!==1||!Number.isSafeInteger(value.seq)
            ||!/^[a-f0-9]{64}$/.test(value.digest))throw Object.assign(Error('recorder_write_unconfirmed'),{terminal:true});
        counter++;return value;
      }catch(error){last=error;if(error.terminal)break;}
    }
    throw Error('recorder_write_unconfirmed');
  };
};
