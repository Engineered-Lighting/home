/* Incremental verification for the Lab's deliberately narrow portable TAR format.
 * Buffers at most one registered object (64 MiB), never a whole session. */
(function(root){
  'use strict';
  // Booted by index.html and executed again by the feature loader: keep the first.
  if(root.HomePerceptionLabArchive)return;
  const decoder=new TextDecoder('utf-8',{fatal:true}), MAX=64*1024*1024;
  function bad(){throw new Error('lab_invalid_archive');}
  function verifier(binding){
    let header=new Uint8Array(512),headerAt=0,entry=null,padding=0,zeros=0,complete=null;
    let pages=0,records=0,media=0,bytes=0,metadataBytes=0,readme=false;
    const pending=new Map();
    function json(data){try{return JSON.parse(decoder.decode(data));}catch(_){bad();}}
    function same(value){if(value.snapshot_id!==binding.snapshotId||value.generation!==binding.generation)bad();}
    async function finishEntry(){
      const {name,data}=entry;
      if(name==='README.json'){
        const v=json(data);same(v);
        if(readme||v.format!=='perception-lab-portable-v1'||v.site_id!==binding.siteId||v.session_id!==binding.sessionId||v.training_ready!==false)bad();
        readme=true;
      } else if(name.startsWith('metadata/')){
        if(!readme||pending.size||name!=='metadata/page-'+String(pages).padStart(6,'0')+'.json')bad();
        metadataBytes+=data.length;if(metadataBytes>MAX)bad();
        const v=json(data);same(v);
        if(v.site_id!==binding.siteId||v.session_id!==binding.sessionId||v.cursor!==records||!Array.isArray(v.records)||v.records.length>200)bad();
        for(const r of v.records){
          if(!['session','annotation','event','job','media'].includes(r.kind)||!r.value)bad();
          if(r.kind==='media'){
            const m=r.value;
            if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(m.media_id)||m.archive_path!=='media/'+m.media_id+'.bin'||
              pending.has(m.archive_path)||m.session_id!==binding.sessionId||!Number.isSafeInteger(m.size)||m.size<0||m.size>MAX||!/^[a-f0-9]{64}$/.test(m.sha256))bad();
            pending.set(m.archive_path,m);
          }
        }
        records+=v.records.length;pages++;
      } else if(name.startsWith('media/')){
        const m=pending.get(name);if(!m||data.length!==m.size)bad();
        const sum=new Uint8Array(await root.crypto.subtle.digest('SHA-256',data));
        if(Array.from(sum,v=>v.toString(16).padStart(2,'0')).join('')!==m.sha256)bad();
        pending.delete(name);media++;bytes+=data.length;
      } else if(name==='COMPLETE.json'){
        const v=json(data);same(v);
        if(!readme||pending.size||v.format!=='perception-lab-portable-v1'||v.pages!==pages||v.media_count!==media||
          v.media_bytes!==bytes||v.total_records!==records||v.media_integrity!=='sha256_verified'||v.training_ready!==false)bad();
        complete=v;
      } else bad();
      padding=(512-data.length%512)%512;entry=null;
    }
    function parseHeader(){
      if(header.every(v=>v===0)){zeros++;if(!complete||zeros>2)bad();return;}
      if(zeros||complete)bad();
      const text=(a,b)=>decoder.decode(header.subarray(a,b)).replace(/\0.*$/s,'');
      const octal=(a,b)=>{const s=text(a,b).trim();if(!/^[0-7]+$/.test(s))bad();return parseInt(s,8);};
      const expected=octal(148,156);let sum=0;
      for(let i=0;i<512;i++)sum+=i>=148&&i<156?32:header[i];
      if(expected!==sum||![0,48].includes(header[156])||text(257,262)!=='ustar'||text(345,500))bad();
      const name=text(0,100),size=octal(124,136);
      if(!Number.isSafeInteger(size)||size<0||size>MAX||(!name.startsWith('media/')&&size>1100000))bad();
      if(name.startsWith('media/')&&(!pending.has(name)||pending.get(name).size!==size))bad();
      entry={name,data:new Uint8Array(size),at:0};
    }
    return {
      async push(chunk){
        if(!(chunk instanceof Uint8Array)||chunk.length>4*1024*1024)bad();
        let offset=0;
        while(offset<chunk.length){
          if(padding){const n=Math.min(padding,chunk.length-offset);if(chunk.subarray(offset,offset+n).some(v=>v!==0))bad();padding-=n;offset+=n;continue;}
          if(entry){const n=Math.min(entry.data.length-entry.at,chunk.length-offset);entry.data.set(chunk.subarray(offset,offset+n),entry.at);entry.at+=n;offset+=n;
            if(entry.at===entry.data.length)await finishEntry();continue;}
          const n=Math.min(512-headerAt,chunk.length-offset);header.set(chunk.subarray(offset,offset+n),headerAt);headerAt+=n;offset+=n;
          if(headerAt===512){parseHeader();headerAt=0;if(entry&&entry.data.length===0)await finishEntry();}
        }
      },
      finish(){if(!complete||zeros!==2||headerAt||entry||padding)bad();return {...complete,verified:true};}
    };
  }
  root.HomePerceptionLabArchive=Object.freeze({verifier});
})(window);
