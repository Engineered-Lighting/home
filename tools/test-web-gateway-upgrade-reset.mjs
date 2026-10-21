import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import http from 'node:http';
import net from 'node:net';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const listen=async server=>{server.listen(0,'127.0.0.1');await once(server,'listening');return server.address().port;};
const health=port=>new Promise((resolve,reject)=>{
  const request=http.get({host:'127.0.0.1',port,path:'/healthz',timeout:1000},response=>{
    response.resume();response.on('end',()=>resolve(response.statusCode));
  });
  request.on('error',reject);request.on('timeout',()=>request.destroy(new Error('fixture_health_timeout')));
});

for(const side of ['client','upstream'])test('resetting an upgraded '+side+' closes its tunnel without terminating the gateway',{timeout:20000},async()=>{
  const sockets=new Set();
  const upstream=net.createServer(socket=>{
    sockets.add(socket);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));
    socket.once('data',()=>socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n'));
  });
  const targetPort=await listen(upstream),probe=net.createServer(),port=await listen(probe);
  await new Promise(resolve=>probe.close(resolve));
  let stderr='',client;
  const child=spawn(process.execPath,['web-gateway/server.mjs'],{cwd:root,env:{...process.env,
    HOME_WEB_HOST:'127.0.0.1',HOME_WEB_PORT:String(port),HOME_WEB_AUTH_REQUIRED:'0',
    HOME_WEB_ENABLE_LEGACY_HA_PROXY:'1',HOME_WEB_HA_TARGET:'http://127.0.0.1:'+targetPort,
    HOME_WEB_HA_TOKEN_FILE:'',HOME_WEB_ASSET_VERSION:'authored-upgrade-reset'},stdio:['ignore','ignore','pipe']});
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16384);});
  try{
    let ready=false;
    for(let i=0;i<100&&!ready;i++){
      assert.equal(child.exitCode,null,'gateway exited before readiness: '+stderr);
      try{ready=await health(port)===200;}catch{await pause(25);}
    }
    assert.ok(ready,'gateway did not start');
    client=net.connect(port,'127.0.0.1');client.on('error',()=>{});await once(client,'connect');
    client.write('GET /proxy/ha/api/websocket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n');
    const [reply]=await once(client,'data');assert.match(reply.toString(),/^HTTP\/1.1 101/);
    if(side==='client')client.resetAndDestroy();else [...sockets][0].resetAndDestroy();
    await pause(150);
    assert.equal(child.exitCode,null,'a TCP reset must not crash Home: '+stderr);
    assert.equal(await health(port),200,'unrelated HTTP service remains available');
    for(let i=0;i<30&&sockets.size;i++)await pause(10);
    assert.equal(sockets.size,0,'the abandoned upstream tunnel must close');
  }finally{
    client?.destroy();for(const socket of sockets)socket.destroy();
    await new Promise(resolve=>upstream.close(resolve));
    if(child.exitCode===null){child.kill();await once(child,'exit');}
  }
});
