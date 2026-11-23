// Headless local-browser QA using built-in Node APIs; no user browser profile.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i]]=process.argv[i+1];
for(const key of ['--browser','--profile','--output-dir','--url'])assert(options[key],`Missing ${key}`);
const base=new URL(options['--url']);assert.equal(base.hostname,'127.0.0.1');
const output=path.resolve(options['--output-dir']);
const profile=path.resolve(options['--profile']);
assert(output!==profile&&!output.startsWith(profile+path.sep)&&!profile.startsWith(output+path.sep),'Browser profile and exported output must be separate');
try{assert.equal((await fs.readdir(profile)).length,0,'Use a new or empty isolated browser profile');}catch(error){if(error.code!=='ENOENT')throw error;}
try{assert.equal((await fs.readdir(output)).length,0,'Output must be new or empty');}catch(error){if(error.code!=='ENOENT')throw error;}
await fs.mkdir(output,{recursive:true});await fs.mkdir(options['--profile'],{recursive:true});
const browser=spawn(options['--browser'],['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${path.resolve(options['--profile'])}`,'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
let endpoint='';let stderr='';browser.stderr.on('data',data=>{stderr+=String(data);const match=stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(match)endpoint=match[1];});
const sockets=[];
async function until(fn,label){const deadline=Date.now()+20000;while(Date.now()<deadline){const result=await fn();if(result)return result;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out: '+label);}
async function connect(url){const ws=new WebSocket(url);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});sockets.push(ws);let id=0;const pending=new Map();const events=[];ws.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.id){const pair=pending.get(message.id);if(pair){pending.delete(message.id);clearTimeout(pair.timer);message.error?pair.reject(Error(JSON.stringify(message.error))):pair.resolve(message.result);}}else events.push(message);});ws.addEventListener('close',event=>{for(const pair of pending.values()){clearTimeout(pair.timer);pair.reject(Error('CDP connection closed: '+event.code+' '+event.reason));}pending.clear();});return {events,send(method,params={}){return new Promise((resolve,reject)=>{const current=++id;const timer=setTimeout(()=>{pending.delete(current);reject(Error('CDP timeout: '+method));},15000);pending.set(current,{resolve,reject,timer});ws.send(JSON.stringify({id:current,method,params}));});}};}
async function evaluate(client,expression){const reply=await client.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(reply.exceptionDetails)throw Error(JSON.stringify(reply.exceptionDetails));return reply.result.value;}
async function screenshot(client,name){const reply=await client.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(output,name),Buffer.from(reply.data,'base64'));}
let report;
try{
  await until(()=>endpoint,'browser startup');const remote=new URL(endpoint);const targetList=()=>fetch(`http://${remote.host}/json/list`).then(r=>r.json());
  const page=await until(async()=>{const list=await targetList();return list.find(t=>t.type==='page');},'page target');
  const client=await connect(page.webSocketDebuggerUrl);await client.send('Page.enable');await client.send('Network.enable');
  await client.send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await client.send('Page.navigate',{url:new URL('/#evidence',base).href});
  const iframeExpression="document.querySelector('#panel-evidence iframe')?.contentDocument";
  await until(()=>evaluate(client,`(()=>{const d=${iframeExpression};return d?.querySelector('#count-done')?.textContent==='5'&&d?.querySelectorAll('.card').length===7;})()`),'workspace tab and results');
  const workspace=await evaluate(client,`(()=>{const d=${iframeExpression};return {selected:document.querySelector('#tab-evidence').getAttribute('aria-selected'),banner:d.querySelector('#banner').textContent,cards:d.querySelectorAll('.card').length};})()`);
  assert.equal(workspace.selected,'true');assert.match(workspace.banner,/95个文件/);
  await screenshot(client,'01_工作台优化成果.png');
  const requestsBefore=client.events.filter(e=>e.method==='Network.requestWillBeSent'&&e.params.request.url.includes('/fdna-results/status')).length;
  await evaluate(client,`${iframeExpression}.querySelector('#refresh').click()`);
  await until(async()=>{const count=client.events.filter(e=>e.method==='Network.requestWillBeSent'&&e.params.request.url.includes('/fdna-results/status')).length;return count>requestsBefore&&await evaluate(client,`!${iframeExpression}.querySelector('#refresh').disabled`);},'material refresh');
  const reading=await evaluate(client,`(()=>{const a=${iframeExpression}.querySelector('.card a');const href=a.href;a.click();return href;})()`);
  const readingTarget=await until(async()=>{const list=await targetList();return list.find(t=>t.url===reading);},'report opened in new tab');
  const reader=await connect(readingTarget.webSocketDebuggerUrl);await reader.send('Page.enable');await reader.send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await until(()=>evaluate(reader,"document.images.length>0&&document.images[0].complete&&document.images[0].naturalWidth>0"),'first report chart');
  const reportView=await evaluate(reader,"({title:document.title,images:document.images.length,firstImageWidth:document.images[0].naturalWidth,hasParagraphs:document.querySelectorAll('p').length>2})");
  assert.equal(reportView.images,6);assert.equal(reportView.hasParagraphs,true);await screenshot(reader,'02_图表与说明阅读.png');
  await client.send('Page.navigate',{url:new URL('/fdna-results',base).href});
  await client.send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await until(()=>evaluate(client,"document.querySelector('#count-done')?.textContent==='5'&&!document.querySelector('#refresh').disabled"),'mobile result page');
  const mobile=await evaluate(client,"({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,cards:document.querySelectorAll('.card').length,history:document.querySelector('#history').textContent})");
  assert(mobile.scrollWidth<=mobile.width+1,'Mobile page must not overflow horizontally');assert.match(mobile.history,/没有重新执行/);
  await screenshot(client,'03_手机宽度布局.png');
  report={status:'passed',method:'local_headless_Edge_CDP',url:base.href,checked_at_utc:new Date().toISOString(),
    workspace,refresh_created_new_material_request:true,report_opened_new_tab:true,reportView,mobile,
    limitation:'automated browser checks; native UI helper failed to initialize; no manual UI acceptance claimed'};
  await fs.writeFile(path.join(output,'browser_check.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
}finally{
  if(endpoint){try{const control=await connect(endpoint);await control.send('Browser.close');}catch{}}
  for(const socket of sockets)socket.close();
  if(browser.exitCode===null)browser.kill();
}
