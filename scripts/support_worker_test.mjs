import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const m = await import(String(pathToFileURL(resolve('dist/worker.js'))));
const worker = m.default;
const store = new Map();
const r2 = {get:(k)=>Promise.resolve(store.has(k)?{text:()=>Promise.resolve(store.get(k))}:null),
 put:(k,v)=>{store.set(k,v);return Promise.resolve(null)},
 delete:(k)=>{store.delete(k);return Promise.resolve(null)},
 list:(o)=>Promise.resolve({objects:[...store.keys()].filter(k=>k.startsWith(o.prefix)).map(k=>({key:k}))})};
const pending=[];
const ctx={waitUntil:(p)=>pending.push(Promise.resolve(p).catch(e=>console.error('BG-ERR:',e&&e.message)))};
const env={OPPAI_R2:r2,OPPAI_PREVIEW_SPONSOR_TOKEN:'test-only-secret'};
// fetch stub: video preview returns a done job, everything else daily_limit 429
globalThis.fetch = (target, init) => {
  if (String(target).includes('/preview/video'))
    return Promise.resolve(new Response(JSON.stringify({status:'done',artifactUrl:'/api/v1/preview/video/jobs/9cd52d7c-8727-4f96-aa96-df891fe03caf/artifact'})));
  return Promise.resolve(new Response(JSON.stringify({error:{code:'daily_limit',message:'quota'}}), {status:429}));
};
const req=(path,body,ip)=>new Request('https://oppai.fans'+path,{method:'POST',headers:{origin:'https://oppai.fans','content-type':'application/json','cf-connecting-ip':ip},body:JSON.stringify(body)});
const valid={category:'bug',message:'first visit quota issue',page:'#image',error_id:''};
const free={model:'waiREALMIX_v11',prompt:'DO_NOT_LOG_PROMPT',publication_consent:'public-examples-v1'};
const call=async(path,body,ip,expected)=>{const r=await worker.fetch(req(path,body,ip),env,ctx);assert.equal(r.status,expected);return r.json()};
try{
  const step=(s)=>console.log('STEP',s);
  for(let i=0;i<10;i++) await call('/api/feedback',valid,'192.0.2.1',201);
  step('0');await call('/api/feedback',valid,'192.0.2.1',429);
  step('1');await call('/api/feedback',valid,'192.0.2.2',201);
  await call('/api/feedback',{...valid,message:''},'192.0.2.2',400);
  await call('/api/feedback',{...valid,message:'x'.repeat(9000)},'192.0.2.2',503);
  
{const r=await worker.fetch(req('/api/free/image',free,'192.0.2.1'),env,ctx);
 console.log('IMG STATUS',r.status);console.log('IMG BODY',await r.clone().text());
 await Promise.all(pending);
 console.log('STORE KEYS',[...store.keys()].filter(k=>k.startsWith('site-errors')).length);
 for(const [k,v] of store){if(k.startsWith('site-errors'))console.log('REC',k,v);}}
  const b1=await call('/api/free/image',free,'192.0.2.2',429);
  assert.match(b1.request_id,/^[a-f0-9-]{36}$/);
  const b2=await call('/api/free/video',{model:'10eros-max'},'192.0.2.1',200);
  assert.equal(b2.artifactUrl,'/api/free/video/jobs/9cd52d7c-8727-4f96-aa96-df891fe03caf/artifact');
  await Promise.all(pending);
  // persistence + privacy
  const fb=[...store.keys()].filter(k=>k.startsWith('feedback/'));
  const errs=[...store.keys()].filter(k=>k.startsWith('site-errors/'));
  assert.equal(fb.length,11); assert.ok(errs.length>0);
  const blob=JSON.stringify([...store.entries()].filter(([k])=>k.startsWith('site-errors')).map(([,v])=>v));
  if(blob.includes('DO_NOT_LOG_PROMPT')){console.log('LEAK BLOB:',blob.slice(0,3000));}assert.equal(blob.includes('DO_NOT_LOG_PROMPT'),false);
  if(blob.includes('192.0.2.')){for(const [k,v] of store){if(String(v).includes('192.0.2.'))console.log('IP-REC:',k,String(v).slice(0,300));}}assert.equal(blob.includes('192.0.2.'),false);
  assert.equal(blob.includes('first visit'),false);
  console.log('PRE-ASSERT site-errors:',[...store.entries()].filter(([k])=>k.startsWith('site-errors')).map(([k,v])=>String(v).slice(0,160)).join(' || '));assert.equal(blob.includes('daily_limit'),true);
  // retention
  for(const [k,v] of store){ if(k!=='feedback-count'){const o=JSON.parse(v);if(o['created-at']!==undefined)o['created-at']=0;if(o['expires-at']!==undefined)o['expires-at']=0;store.set(k,JSON.stringify(o));}}
  await worker.scheduled(null,env,ctx);
  await Promise.all(pending);
  console.log('POST-SWEEP keys:',[...store.keys()].join(','));assert.equal([...store.keys()].filter(k=>k.startsWith('feedback/')).length,0);
  assert.equal([...store.keys()].filter(k=>k.startsWith('site-errors/')).length,0);
  console.log('Support integration passed: persistence, limits, isolation, spoof rejection, privacy, retention, error IDs');
}catch(e){console.error('FAIL:',e&&e.stack||e);process.exit(1)}
process.exit(0);
