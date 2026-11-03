// Copy to tmp (junction to bundled artifact-tool node_modules), run from repo root.
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {FileBlob,SpreadsheetFile,Workbook} from '@oai/artifact-tool';

const root=process.cwd(), output=path.join(root,'excel_entry/templates');
const previews=path.join(root,'tmp/unified-templates');
await fs.mkdir(previews,{recursive:true});
const spec=JSON.parse(execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-B','-m','excel_entry.template_spec'],{encoding:'utf8'}));
function write(sheet,rows,color='#284466'){
  const width=rows[0].length;
  const range=sheet.getRangeByIndexes(0,0,rows.length,width);
  range.values=rows.map(row=>Array.from({length:width},(_,i)=>row[i]??null));
  range.format.font={name:'Arial',size:11,color:'#243247'};
  range.format.rowHeight=28;range.format.columnWidth=24;range.format.verticalAlignment='center';
  sheet.getRangeByIndexes(0,0,1,width).format={fill:color,font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:34,wrapText:true};
  for(let c=0;c<width;c++){
    const max=Math.max(...rows.map(row=>[...String(row[c]??'')].reduce((n,ch)=>n+(ch.charCodeAt(0)>255?2:1),0)));
    sheet.getRangeByIndexes(0,c,rows.length,1).format.columnWidth=Math.min(48,Math.max(18,max+2));
  }
  if(width===2){sheet.getRangeByIndexes(0,1,rows.length,1).format.columnWidth=100;range.format.wrapText=true;range.format.autofitRows();}
  if(width>2&&rows.length>1){range.format.wrapText=true;range.format.autofitRows();}
  for(let r=1;r<rows.length;r+=2)sheet.getRangeByIndexes(r,0,1,width).format.fill='#F0F4F8';
  sheet.freezePanes.freezeRows(1);sheet.showGridLines=false;sheet.tabColor=color;
}
for(const item of spec.templates){
  if(process.argv[2] && item.filename!==process.argv[2])continue;
  const wb=item.source?Workbook.create():await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(output,item.filename)));
  console.log(item.filename,(await wb.inspect({kind:'sheet',include:'id,name',maxChars:1600})).ndjson);
  if(!item.source){
    const before=await wb.render({sheetName:'任务',range:'A1:G3',scale:1,format:'png'});
    await fs.writeFile(path.join(previews,item.filename+'-before.png'),new Uint8Array(await before.arrayBuffer()));
  }else for(const sheet of item.source)write(wb.worksheets.add(sheet.name),sheet.rows);
  // Existing source sheets remain intact; only extend their usage guide.
  const guide=wb.worksheets.getItem('填写指南');
  write(guide,spec.guide);
  for(const name of Object.keys(item.extension)){
    // Builder can be rerun on an already expanded template.
    let sheet;try{sheet=wb.worksheets.getItem(name)}catch{sheet=wb.worksheets.add(name)}
    if(!sheet)sheet=wb.worksheets.add(name);
    sheet.getUsedRange()?.clear({applyTo:'contents'});
    write(sheet,item.extension[name],'#37654C');
  }
  wb.recalculate();
  console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:5},maxChars:1000})).ndjson);
  for(const name of ['填写指南','设计设置','设计任务','法器','来源取值','规则','协同关系']){
    const preview=await wb.render({sheetName:name,range:name==='法器'?'A1:G5':name==='填写指南'?'A1:B18':'A1:G5',scale:1,format:'png'});
    await fs.writeFile(path.join(previews,item.filename+'-'+name+'.png'),new Uint8Array(await preview.arrayBuffer()));
  }
  const xlsx=await SpreadsheetFile.exportXlsx(wb);
  await xlsx.save(path.join(previews,item.filename));
  await fs.copyFile(path.join(previews,item.filename),path.join(output,item.filename));
}
