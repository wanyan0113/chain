// Presentation of engine reports only; graph layout never selects or infers chains.
const ROLES = ['insight', 'coordination', 'manifestation'];
const LABELS = {target:'目标', insight:'探知法器', coordination:'协调法器', manifestation:'具现法器'};
const COLORS = {target:'#7eacc6', insight:'#80c8b6', coordination:'#f5ad69', manifestation:'#f36779'};
const NS = 'http://www.w3.org/2000/svg';
const identity = (kind,id) => JSON.stringify([kind,id]);
export const rowLabel = index => index < 20 ? String.fromCodePoint(0x2460+index) : `${index+1}.`;

export function buildGraph(report, chainId=null, {allCandidates=false}={}) {
  const all=report.tasks.flatMap(task=>task.chains.map(chain=>({task,chain})));
  const ids=allCandidates ? all.map(item=>item.chain.id) : chainId===null ? (report.network?.chains??[]) : [chainId];
  if(new Set(ids).size!==ids.length) throw Error('报告包含重复链编号');
  const selected=ids.map(id=>{
    const matches=all.filter(item=>item.chain.id===id);
    if(matches.length!==1) throw Error(`无法唯一定位链：${id}`);
    return matches[0];
  });
  const mode=allCandidates?'candidates':chainId===null?'network':'chain';
  const nodeMap=new Map(),edgeMap=new Map(),chains=[];
  function node(kind,id,column,chain,task) {
    // Left and right targets are two occurrences of the same business object.
    // Network artifacts merge by identity; chain rows remain separate drawings.
    const objectKey=identity(kind,id);
    const key=JSON.stringify([objectKey,column,mode==='network'?(kind==='target'?task:'shared'):chain]);
    if(!nodeMap.has(key)) nodeMap.set(key,{key,objectKey,kind,label:id,column,role:kind==='artifact'?ROLES[column-1]:'target',chains:[]});
    const item=nodeMap.get(key);
    if(!item.chains.includes(chain)) item.chains.push(chain);
    return key;
  }
  selected.forEach(({task,chain},index)=>{
    chains.push({id:chain.id,row:index,task:task.task,target:task.target,
      selected:report.network?.chains.includes(chain.id)??false,metrics:chain.metrics,score:chain.score,
      start_s:chain.start_s,end_s:chain.end_s,evidence:{task_rules:task.inference,
        required_capabilities:task.required_capabilities,
        artifacts:task.selection.filter(s=>Object.values(chain.artifacts).includes(s.artifact)),
        links:chain.link_evidence,blockers:task.blockers}});
    const route=[node('target',task.target,0,chain.id,task.task),
      ...ROLES.map((role,i)=>node('artifact',chain.artifacts[role],i+1,chain.id,task.task)),
      node('target',task.target,4,chain.id,task.task)];
    const labels=['探知','传讯','传讯与统筹','具现'];
    route.slice(0,-1).forEach((from,i)=>{
      const to=route[i+1],key=JSON.stringify([from,to]);
      if(!edgeMap.has(key)) edgeMap.set(key,{from,to,label:labels[i],kind:i===1||i===2?'cooperation':'flow',chains:[],evidence:[]});
      const edge=edgeMap.get(key);
      edge.chains.push(chain.id);
      if(i===1||i===2) edge.evidence.push({chain:chain.id,link:chain.link_evidence[i-1]});
    });
  });
  const columns=Array.from({length:5},()=>[]);
  for(const n of nodeMap.values()) columns[n.column].push(n);
  const rows=Math.max(1,...columns.map(col=>col.length));
  const top=126,pitch=120;
  for(const col of columns) col.forEach((n,i)=>{
    n.x=108+n.column*224;
    n.y=mode==='network'?top+(i+.5)*(rows*pitch/col.length):top+chains.findIndex(c=>c.id===n.chains[0])*pitch+24;
  });
  return {mode,nodes:[...nodeMap.values()],edges:[...edgeMap.values()],chains,
    relations:mode==='network'?(report.network?.relations??[]):[],width:1112,
    height:mode==='network'?top+rows*pitch+70:top+rows*pitch+30,status:report.status,scope:report.scope};
}

export function buildReferenceGraph(result,{network=false}={}) {
  if(result.status!=='reference_reproduced') throw Error('需要文章案例复算结果');
  const statements=network?result.reported_topology:result.reported_topology.slice(0,1);
  // A presentation adapter only: these statements are never design-engine inputs.
  const projection={status:'reference_reproduced',scope:result.scope,
    tasks:statements.map(s=>({task:s.mission,target:s.target,inference:[],required_capabilities:{},selection:[],blockers:[],
      chains:[{id:s.id,artifacts:Object.fromEntries(ROLES.map(r=>[r,s.artifacts[r].id])),link_evidence:[],
        metrics:s.metrics,score:s.score}]})),
    network:network?{chains:statements.map(s=>s.id),relations:[]}:null};
  const model=buildGraph(projection,network?null:statements[0].id);
  model.mode=network?'reference-network':'reference-chain';
  model.referenceOnly=true;
  model.title=network?'原文陈述的两条链（未独立验收）':'正文明确给出的最优链（评分已复算，筛选未独立验收）';
  for(const chain of model.chains) {
    const statement=statements.find(s=>s.id===chain.id);
    chain.selected=false;
    chain.referenceOnly=true;
    chain.evidence={source_refs:statement.source_refs,independent_feasibility:'not_verified',
      evaluation_row:statement.evaluation_row,metrics:statement.metrics,score:statement.score};
  }
  for(const node of model.nodes) {
    if(node.kind==='artifact') {
      const statement=statements.find(s=>node.chains.includes(s.id));
      node.platform=Object.values(statement.artifacts).find(a=>a.id===node.label).platform;
    }
  }
  for(const edge of model.edges) {
    edge.kind='reported-flow';
    edge.label='原文链内顺序（协同未独立验收）';
    edge.evidence=edge.chains.map(id=>({chain:id,source_refs:statements.find(s=>s.id===id).source_refs,independent_feasibility:'not_verified'}));
  }
  return model;
}

function element(name,attributes={},text=null) {
  const el=document.createElementNS(NS,name);
  for(const [key,value] of Object.entries(attributes)) el.setAttribute(key,String(value));
  if(text!==null) el.textContent=text;
  return el;
}

function labelLines(label) {
  const lines=[''];let width=0;
  for(const ch of String(label)) {
    const size=ch.codePointAt(0)>255?2:1;
    if(width+size>25){lines.push('');width=0;}
    lines[lines.length-1]+=ch;width+=size;
  }
  return lines.length>3?[lines[0],lines[1],lines[2].slice(0,-1)+'…']:lines;
}

export function renderGraph(container,model,onInspect=()=>{}) {
  container.replaceChildren();
  if(!model.chains.length) {
    const p=document.createElement('p');
    p.textContent=model.mode==='network'?'尚无选中网络。请查看阻塞或冲突原因；可切换到候选链查看已有方案。':'尚无可展示的候选链。';
    container.append(p);return null;
  }
  const network=model.mode==='network'||model.mode==='reference-network';
  const heading=model.title??(model.mode==='network'?'选中网络':model.mode==='candidates'?'候选链（每行一条，选中情况见图外对应表）':model.chains[0].selected?'选中网络内的单条链':'候选链预览（尚未被网络选中）');
  const svg=element('svg',{xmlns:NS,viewBox:`0 0 ${model.width} ${model.height}`,width:model.width,height:model.height,
    role:'img','aria-label':heading});
  svg.append(element('title',{},heading));
  svg.append(element('desc',{},`${model.scope}。任务及评价指标在图外展示。两端同名目标表示同一对象，右端为作用终点。`));
  svg.append(element('rect',{width:'100%',height:'100%',fill:'#ffffff'}));
  svg.append(element('text',{x:24,y:30,'font-size':17,'font-family':'sans-serif',fill:'#263445'},heading));
  Object.keys(COLORS).forEach((role,i)=>{
    svg.append(element('circle',{cx:34+i*134,cy:66,r:7,fill:COLORS[role]}));
    svg.append(element('text',{x:48+i*134,y:71,'font-size':13,'font-family':'sans-serif',fill:'#536171'},LABELS[role]));
  });
  const defs=element('defs'),marker=element('marker',{id:'network-arrow',viewBox:'0 0 10 10',refX:9,refY:5,
    markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});
  marker.append(element('path',{d:'M 0 1 L 9 5 L 0 9 z',fill:'#454a52'}));defs.append(marker);svg.append(defs);
  const nodes=new Map(model.nodes.map(n=>[n.key,n]));
  const radius=21;
  for(const edge of model.edges) {
    const a=nodes.get(edge.from),b=nodes.get(edge.to),dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy);
    const x1=a.x+dx/len*(radius+1),y1=a.y+dy/len*(radius+1);
    const x2=b.x-dx/len*(radius+3),y2=b.y-dy/len*(radius+3);
    const path=element('path',{d:`M ${x1} ${y1} L ${x2} ${y2}`,fill:'none',stroke:'#454a52',
      'stroke-width':1.4,'marker-end':'url(#network-arrow)','data-chains':JSON.stringify(edge.chains)});
    path.append(element('title',{},`${a.label} → ${b.label}（${edge.label}）；链：${edge.chains.join('、')}`));svg.append(path);
  }
  if(!network) for(const chain of model.chains) svg.append(element('text',{
    x:22,y:150+chain.row*120+5,'font-size':19,'font-family':'sans-serif',fill:'#454a52'},rowLabel(chain.row)));
  for(const node of model.nodes) {
    const side=node.kind==='target'?(node.column===0?'起点':'终点'):'';
    const g=element('g',{tabindex:0,role:'button','aria-label':`${LABELS[node.role]} ${node.label} ${side}，用于${node.chains.length}条链，查看依据`,style:'cursor:pointer'});
    g.append(element('title',{},`${node.label}；${side||LABELS[node.role]}；${node.chains.join('、')}`));
    g.append(element('circle',{cx:node.x,cy:node.y,r:radius,fill:COLORS[node.role],stroke:'#ffffff','stroke-width':1}));
    const text=element('text',{x:node.x,y:node.y+42,'text-anchor':'middle','font-size':14,'font-family':'sans-serif',fill:'#27313d'});
    const lines=labelLines(node.label);
    if(node.platform) lines.push(`（${node.platform}）`);
    lines.forEach((line,i)=>text.append(element('tspan',{x:node.x,dy:i?18:0},line)));
    g.append(text);
    const inspect=()=>onInspect(node,model.chains.filter(c=>node.chains.includes(c.id)));
    g.addEventListener('click',inspect);
    g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();inspect();}});
    svg.append(g);
  }
  svg.append(element('text',{x:24,y:model.height-16,'font-size':12,'font-family':'sans-serif',fill:'#75808c'},
    model.referenceOnly?'箭头仅呈现原文陈述的顺序；未独立验证传讯、资源可用性或仿真结果。':'两端同名目标为同一对象；箭头表示链内流程与协同方向，不表示物理坐标或时间。'));
  container.append(svg);return svg;
}

export function graphSvg(svg) {
  return '<?xml version="1.0" encoding="UTF-8"?>\n'+new XMLSerializer().serializeToString(svg);
}
