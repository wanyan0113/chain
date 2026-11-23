'use strict';
const $=id=>document.getElementById(id);
function node(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function link(text,url){const a=node('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.setAttribute('aria-label',text+'（新标签页）');return a;}
function time(value){return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';}
function render(data){
  const ok=data.status==='passed';$('banner').className='banner '+(ok?'good':'bad');
  $('banner').textContent=`当前发布材料${ok?'检查通过':'存在检查问题'}：${data.packages.length}组、${data.package_files_checked}个文件；${time(data.checked_at_utc)}。本次没有执行数值重算。`;
  $('count-done').textContent=data.declared_progress_counts.implemented_scoped;$('count-partial').textContent=data.declared_progress_counts.partial;$('count-deferred').textContent=data.declared_progress_counts.deferred;
  $('priorities').replaceChildren();for(const p of data.priorities){const tr=node('tr');tr.append(node('td',`${p.number}. ${p.title}`));const td=node('td');td.append(node('span',p.state_label,'badge '+p.state));tr.append(td,node('td',p.scope),node('td',p.remaining));$('priorities').append(tr);}
  $('gallery').replaceChildren();for(const p of data.packages){const card=node('article',undefined,'card');if(p.thumbnail_url){const img=node('img');img.src=p.thumbnail_url;img.alt=p.title+'图表预览';img.loading='lazy';card.append(img);}const details=node('div',undefined,'details');details.append(node('h3',p.title),node('span',p.status==='passed'?'材料检查通过':'材料检查失败','badge '+(p.status==='passed'?'':'failed')),node('p',`${p.figure_count}张图 · ${p.files_checked}个已检查文件`,'small muted'));if(p.status==='passed')details.append(link('查看图表与完整说明 →',p.reading_url));else{const errors=node('ul',undefined,'failure-list small');for(const e of p.errors)errors.append(node('li',`${e.file}：${e.error}`));details.append(errors);}card.append(details);$('gallery').append(card);}
  const h=data.historical_full;$('history').replaceChildren();if(h.status!=='available')$('history').append(node('p',h.reason,'muted'));else{
    $('history').append(node('p',`记录时间：${time(h.checked_at_utc)}`),node('p',`当次状态：${h.recorded_status==='passed'?'通过':'未全部通过'}；独立脚本已调用${h.full_verifiers_invoked}项。`),node('p',h.matches_current_materials?'该记录登记的材料版本与当前发布材料一致；本页没有重新执行这些数值任务。':'该记录的目录或材料版本与当前不同，请勿将历史结果当成本次复核。','notice small'));
    const details=node('details');details.append(node('summary','查看当次任务列表'));const ul=node('ul');ul.id='history-tasks';for(const t of h.tasks)ul.append(node('li',`${t.title}：${t.status==='passed'?'通过':'失败'}${t.verifier_invoked?'':'（未调用）'}`));details.append(ul);$('history').append(details,link('阅读该次完整复核记录 →',h.reading_url));
  }
  $('documents').replaceChildren();for(const d of data.documents){if(d.status==='passed')$('documents').append(link(d.title,d.url));else $('documents').append(node('span',d.title+'：版本检查未通过','badge failed'));}
}
async function refresh(){const button=$('refresh');button.disabled=true;$('banner').className='banner';$('banner').textContent='正在检查发布材料…';try{const response=await fetch('/fdna-results/status',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取检查结果');render(data);}catch(error){$('banner').className='banner bad';$('banner').textContent='本次检查未完成：'+error.message+'。下方若有内容，是上次载入的结果。';}finally{button.disabled=false;}}
$('refresh').addEventListener('click',refresh);refresh();
