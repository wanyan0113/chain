'use strict';
const $=id=>document.getElementById(id);
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function link(text,url){const a=node('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.setAttribute('aria-label',text+'（新标签页）');return a;}
function time(value){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';}
function render(data){
  const ok=data.status==='passed';$('banner').className='banner '+(ok?'good':'bad');
  $('banner').textContent=`当前发布材料${ok?'检查通过':'存在检查问题'}：${data.packages.length}组、${data.package_files_checked}个文件；${time(data.checked_at_utc)}。本次材料检查未执行数值重算。`;
  $('count-done').textContent=data.declared_progress_counts.implemented_scoped;$('count-partial').textContent=data.declared_progress_counts.partial;$('count-deferred').textContent=data.declared_progress_counts.deferred;
  $('priorities').replaceChildren();for(const p of data.priorities){const tr=node('tr');tr.append(node('td',`${p.number}. ${p.title}`));const td=node('td');td.append(node('span',p.state_label,'badge '+p.state));tr.append(td,node('td',p.scope),node('td',p.remaining));$('priorities').append(tr);}
  $('gallery').replaceChildren();for(const p of data.packages){const card=node('article',undefined,'card');if(p.thumbnail_url){const img=node('img');img.src=p.thumbnail_url;img.alt=p.title+'图表预览';img.loading='lazy';card.append(img);}const details=node('div',undefined,'details');details.append(node('h3',p.title),node('span',p.status==='passed'?'材料检查通过':'材料检查失败','badge '+(p.status==='passed'?'':'failed')),node('p',`${p.figure_count}张图 · ${p.files_checked}个已检查文件`,'small muted'));if(p.status==='passed')details.append(link('查看图表与完整说明 →',p.reading_url));else{const errors=node('ul',undefined,'failure-list small');for(const e of p.errors)errors.append(node('li',`${e.file}：${e.error}`));details.append(errors);}card.append(details);$('gallery').append(card);}
  const h=data.historical_full;$('history').replaceChildren();if(h.status!=='available')$('history').append(node('p',h.reason,'muted'));else{
    $('history').append(node('p',`记录时间：${time(h.checked_at_utc)}`),node('p',`当次状态：${h.recorded_status==='passed'?'通过':'未全部通过'}；独立脚本已调用${h.full_verifiers_invoked}项。`),node('p',h.matches_current_materials?'该历史记录登记的材料版本与当前发布材料一致；当前网页任务请看“本次网页完整复核”。':'该记录的目录或材料版本与当前不同，请勿将历史结果当成本次复核。','notice small'));
    const details=node('details');details.append(node('summary','查看当次任务列表'));const ul=node('ul');ul.id='history-tasks';for(const t of h.tasks)ul.append(node('li',`${t.title}：${t.status==='passed'?'通过':'失败'}${t.verifier_invoked?'':'（未调用）'}`));details.append(ul);$('history').append(details,link('阅读该次完整复核记录 →',h.reading_url));
  }
  $('documents').replaceChildren();for(const d of data.documents){if(d.status==='passed')$('documents').append(link(d.title,d.url));else $('documents').append(node('span',d.title+'：版本检查未通过','badge failed'));}
}
async function refresh(){const button=$('refresh');button.disabled=true;$('banner').className='banner';$('banner').textContent='正在检查发布材料…';try{const response=await fetch('/fdna-results/status',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取检查结果');render(data);}catch(error){$('banner').className='banner bad';$('banner').textContent='本次检查未完成：'+error.message+'。下方若有内容，是上次载入的结果。';}finally{button.disabled=false;}}
$('refresh').addEventListener('click',refresh);refresh();
let recheckTimer,lastHistoryKey='';
function renderRecheck(data){
  $('start-recheck').disabled=!data.enabled||data.status==='running';
  $('recheck-config').textContent=data.enabled?'来源已由服务端配置。点击后在后台逐项复核，页面仍可查看材料。报告保留在新运行目录。':(data.message||'服务未配置完整复核来源。');
  const labels={idle:'本次服务会话尚未启动任务。',running:'正在执行本次完整复核',passed:'本次完整复核通过',failed:'本次完整复核未全部通过'};
  $('recheck-status').className=data.status==='passed'?'banner good':data.status==='failed'?'banner bad':data.status==='running'?'banner':'muted small';
  $('recheck-status').textContent=(labels[data.status]||'未知状态')+(data.started_at_utc?'；开始于'+time(data.started_at_utc):'')+(data.finished_at_utc?'；结束于'+time(data.finished_at_utc):'')+(data.error?'；'+data.error:'');
  $('recheck-progress').value=data.completed_tasks||0;$('recheck-progress').max=data.total_tasks||10;
  $('recheck-tasks').replaceChildren();for(const t of data.tasks||[]){const state={pending:'等待',running:'运行中',passed:'通过',failed:'失败'}[t.status]||t.status;$('recheck-tasks').append(node('li',t.title+'：'+state+(t.error?'；'+t.error:'')));}
  $('recheck-links').replaceChildren();if(data.report_url){$('recheck-links').append(link('打开本次完整报告',data.report_url),document.createTextNode('　'),link('下载本次JSON报告',data.json_url));}
  if(data.status==='running')recheckTimer=setTimeout(pollRecheck,1000);
  const historyKey=String(data.id)+':'+data.status;if(historyKey!==lastHistoryKey){lastHistoryKey=historyKey;loadJobHistory();}
}
async function pollRecheck(){clearTimeout(recheckTimer);try{const response=await fetch('/fdna-results/recheck/status',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'状态读取失败');renderRecheck(data);}catch(error){$('recheck-status').textContent='无法确认任务状态：'+error.message+'。刷新页面重试不会自动启动新任务。';$('start-recheck').disabled=true;}}
$('start-recheck').addEventListener('click',async()=>{clearTimeout(recheckTimer);$('start-recheck').disabled=true;try{const response=await fetch('/fdna-results/recheck',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await response.json();if(!response.ok)throw Error(data.error||'启动失败');renderRecheck(data);}catch(error){await pollRecheck();$('recheck-status').textContent+='；启动请求未能确认：'+error.message;}});
$('refresh-recheck').addEventListener('click',pollRecheck);
pollRecheck();
async function loadJobHistory(){
  const button=$('refresh-job-history');button.disabled=true;
  try{const response=await fetch('/fdna-results/recheck/history',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'记录读取失败');
    $('local-history-status').textContent=!data.enabled?'服务未配置本地复核来源。':data.records.length?`已读取${data.records.length}条记录。记录时间与状态不代表本次重新运行。`:'暂无本地网页复核记录。';
    $('local-history-rows').replaceChildren();const labels={passed:'通过',failed:'失败',running:'本会话运行中',unconfirmed:'运行状态未确认',invalid:'记录检查未通过'};
    for(const r of data.records){const tr=node('tr');const id=node('td',r.id.slice(0,8));id.title=r.id;const state=node('td');state.append(node('span',labels[r.status]||r.status,'badge '+(r.status==='passed'?'':r.status==='failed'||r.status==='invalid'?'failed':'partial')));const report=node('td');if(r.report_available){report.append(link('打开',r.report_url),document.createTextNode(' / '),link('JSON',r.json_url));}else report.textContent='无可用报告';tr.append(id,node('td',r.started_at_utc?time(r.started_at_utc):'未记录'),state,node('td',r.message||'报告摘要已核对'),report);$('local-history-rows').append(tr);}
  }catch(error){$('local-history-status').textContent='记录读取失败：'+error.message+'。下方若有记录，是上次载入的内容。';}finally{button.disabled=false;}
}
$('refresh-job-history').addEventListener('click',loadJobHistory);
