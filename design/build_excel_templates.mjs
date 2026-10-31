// Run a copy in tmp with node_modules linked to the bundled artifact-tool runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = process.cwd();
const contract = JSON.parse(execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-B', '-c',
  'import json; from design.excel import HEADERS, SETTINGS, GUIDE; print(json.dumps(dict(headers=HEADERS, settings=SETTINGS, guide=GUIDE)))'], {encoding:'utf8'}));
const doc = JSON.parse(await fs.readFile('design/examples/canonical_network_demo.json', 'utf8'));
const roles = {insight:'探知', coordination:'协调', manifestation:'具现'};
const list = xs => xs.join(';');
const ev = o => [list(o.source_refs), list(o.blocking_issue_ids)];
const bool = v => String(v);
function val(v) {
  if (v === null) return ['文本', null, null];
  if (Array.isArray(v)) return ['列表', list(v), null];
  if (typeof v === 'object') return ['数值', v.value, v.unit];
  return [typeof v === 'boolean' ? '布尔' : '文本', typeof v === 'boolean' ? bool(v) : v, null];
}
const example = Object.fromEntries(Object.keys(contract.headers).map(n => [n, []]));
example['设计设置'] = contract.settings.map((k,i) => [k, [doc.scope,doc.weights.time,doc.weights.accuracy,doc.weights.cost,
  doc.policies.equal_metric_score,doc.policies.cost_unit,doc.policies.max_chain_combinations,doc.policies.max_network_combinations][i]]);
example['任务'] = doc.tasks.map(t => [t.id,t.target,t.start_s,...ev(t)]);
example['法器'] = doc.artifacts.map(a => [a.id,roles[a.role],bool(a.available),a.metrics?.duration_s ?? null,
  a.metrics?.accuracy ?? null,a.metrics?.cost.value ?? null,a.metrics?.cost.unit ?? null,a.capacity,bool(a.consumable),a.inventory,a.cooldown_s,...ev(a)]);
example['情境'] = doc.contexts.map(c => [c.task,c.artifact,...ev(c)]);
for (const [kind, objects] of [['任务',doc.tasks],['法器',doc.artifacts],['情境',doc.contexts]]) {
  for (const o of objects) for (const [field,v] of Object.entries(o.attributes)) {
    example['属性'].push([kind,o.id ?? o.task,kind==='情境' ? o.artifact : null,field,...val(v)]);
  }
}
for (const [kind, rules] of [['任务',doc.task_rules],['法器',doc.artifact_rules]]) {
  for (const r of rules) {
    example['规则'].push([kind,r.id,roles[r.role],r.capability,bool(!r.when.length),...ev(r)]);
    for (const c of r.when) example['规则条件'].push([kind,r.id,c.field,c.op,'ref' in c ? '字段':'常量',
      ...('ref' in c ? [null,null,null] : val(c.value)),c.ref ?? null]);
  }
}
example['协同关系'] = doc.links.map(l => [l.from,l.to,bool(l.communicates),bool(l.controls),l.distance.value,l.distance.unit,l.max_distance.value,l.max_distance.unit,...ev(l)]);
example['任务依赖'] = doc.dependencies.map(d => [d.before,d.after,d.gap_s]);
example['资源组'] = doc.resource_groups.map(g => [g.id,g.capacity,list(g.artifacts)]);
example['填写指南'] = contract.guide;
const outputDir = path.join(root, 'excel_entry/templates');
const previewDir = path.join(root, 'tmp/design-templates/previews');
await fs.mkdir(previewDir, {recursive:true});
for (const blank of [false,true]) {
  const wb = Workbook.create();
  for (const [name,headers] of Object.entries(contract.headers)) {
    const s = wb.worksheets.add(name);
    const rows = !blank || name==='填写指南' ? example[name] : name==='设计设置' ? contract.settings.map(k=>[k,null]) : [];
    const matrix = [headers,...rows];
    const region = s.getRangeByIndexes(0,0,matrix.length,headers.length);
    region.values = matrix;
    region.format.font = {name:'Arial',size:11,color:'#243247'};
    region.format.rowHeight = 26;
    region.format.verticalAlignment = 'center';
    region.format.columnWidth = 22;
    s.getRangeByIndexes(0,0,1,headers.length).format = {fill:'#284466',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:32};
    for(let col=0;col<headers.length;col++) {
      const max = Math.max(...matrix.map(r=>[...String(r[col]??'')].reduce((n,ch)=>n+(ch.charCodeAt(0)>255?2:1),0)));
      s.getRangeByIndexes(0,col,matrix.length,1).format.columnWidth = Math.min(48,Math.max(16,max+3));
    }
    if(name==='设计设置' || name==='填写指南') {
      s.getRangeByIndexes(0,1,matrix.length,1).format.columnWidth = 96;
      if(matrix.length>1) {
        s.getRangeByIndexes(1,1,matrix.length-1,1).format.wrapText = true;
        s.getRangeByIndexes(1,1,matrix.length-1,1).format.autofitRows();
      }
    }
    if(name==='规则') {
      s.getRangeByIndexes(0,5,matrix.length,1).format.columnWidth=48;
      if(matrix.length>1) {s.getRangeByIndexes(1,5,matrix.length-1,1).format.wrapText=true;region.format.autofitRows();}
    }
    if(matrix.length>1) for(let r=1;r<matrix.length;r+=2) s.getRangeByIndexes(r,0,1,headers.length).format.fill='#F0F4F8';
    s.freezePanes.freezeRows(1);
    s.showGridLines=false;
  }
  wb.recalculate();
  const scan = await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:10},maxChars:1000});
  console.log(blank?'blank':'example',scan.ndjson);
  for (const name of Object.keys(contract.headers)) {
    console.log((await wb.inspect({kind:'table',range:`'${name}'!A1:C3`,include:'values,formulas',tableMaxRows:3,tableMaxCols:3,maxChars:600})).ndjson);
    const preview=await wb.render({sheetName:name,autoCrop:'all',scale:1,format:'png'});
    await fs.writeFile(path.join(previewDir,`${blank?'blank':'example'}-${name}.png`),new Uint8Array(await preview.arrayBuffer()));
  }
  const output=await SpreadsheetFile.exportXlsx(wb);
  await output.save(path.join(outputDir,blank?'链网设计空白.xlsx':'链网设计示例.xlsx'));
}
