// The view reads engine reports only. It never infers links or selects chains.
const ROLES = ['insight', 'coordination', 'manifestation'];
const LABELS = {task:'任务', target:'目标', insight:'探知法器', coordination:'协调法器', manifestation:'具现法器'};
const COLORS = ['#245b9e', '#a13c76', '#997014', '#197865', '#744eb3', '#9f452d'];
const NS = 'http://www.w3.org/2000/svg';
const key = (kind, id) => JSON.stringify([kind, id]);

export function buildGraph(report, chainId = null) {
  const all = report.tasks.flatMap(task => task.chains.map(chain => ({task, chain})));
  const ids = chainId === null ? (report.network?.chains ?? []) : [chainId];
  if (new Set(ids).size !== ids.length) throw Error('报告包含重复链编号');
  const selected = ids.map(id => {
    const matches = all.filter(item => item.chain.id === id);
    if (matches.length !== 1) throw Error(`无法唯一定位链：${id}`);
    return matches[0];
  });
  const nodes = new Map(), edges = [], chains = [];
  function node(kind, id, column, chain) {
    const idKey = key(kind, id);
    if (!nodes.has(idKey)) nodes.set(idKey, {key:idKey, kind, label:id, column, chains:[]});
    const n = nodes.get(idKey);
    if (n.column !== column) throw Error(`同一法器角色冲突：${id}`);
    if (!n.chains.includes(chain)) n.chains.push(chain);
    return idKey;
  }
  selected.forEach(({task, chain}, index) => {
    const chainView = {id:chain.id, task:task.task, target:task.target, color:COLORS[index % COLORS.length],
      selected:report.network?.chains.includes(chain.id) ?? false, metrics:chain.metrics,
      score:chain.score, start_s:chain.start_s, end_s:chain.end_s, evidence:{
        task_rules:task.inference, required_capabilities:task.required_capabilities,
        artifacts:task.selection.filter(s => Object.values(chain.artifacts).includes(s.artifact)),
        links:chain.link_evidence, blockers:task.blockers}};
    chains.push(chainView);
    const mission = node('task', task.task, 0, chain.id);
    const target = node('target', task.target, 1, chain.id);
    const artifacts = ROLES.map((role,i) => node('artifact', chain.artifacts[role], i+2, chain.id));
    const route = [mission, target, ...artifacts, target];
    const labels = ['任务目标', '探知', '传讯', '传讯与统筹', '具现'];
    route.slice(0,-1).forEach((from,i) => edges.push({from, to:route[i+1], chain:chain.id,
      color:chainView.color, label:labels[i], kind:i===0?'assignment':i===4?'return':i===2||i===3?'cooperation':'flow',
      evidence:i===2||i===3 ? chain.link_evidence[i-2] : null}));
  });
  const columns = Array.from({length:5},()=>[]);
  for (const n of nodes.values()) columns[n.column].push(n);
  const maxRows = Math.max(1,...columns.map(c=>c.length));
  const base = 110 + maxRows * 110;
  for (const column of columns) column.forEach((n,i) => {
    n.x = 22 + n.column * 246;
    n.y = 100 + i*110 + (maxRows-column.length)*55;
    n.role = n.kind === 'artifact' ? ROLES[n.column-2] : n.kind;
  });
  return {mode:chainId===null?'network':'chain', nodes:[...nodes.values()], edges, chains,
    relations:chainId===null ? (report.network?.relations ?? []) : [],
    width:1260, height:base+Math.max(1,chains.length)*28+45, returnY:base,
    status:report.status, scope:report.scope};
}

function svgElement(name, attributes={}, text=null) {
  const el=document.createElementNS(NS,name);
  for(const [k,v] of Object.entries(attributes)) el.setAttribute(k,String(v));
  if(text!==null) el.textContent=text;
  return el;
}

function labelLines(label) {
  const lines=[''];
  let width=0;
  for(const ch of String(label)) {
    const size=ch.codePointAt(0)>255?2:1;
    if(width+size>25) {lines.push('');width=0;}
    lines[lines.length-1]+=ch;width+=size;
  }
  return lines.length>2?[lines[0],lines[1].slice(0,-1)+'…']:lines;
}

export function renderGraph(container, model, onInspect=()=>{}) {
  container.replaceChildren();
  if(!model.chains.length) {
    const p=document.createElement('p');
    p.textContent='尚无选中网络。请查看阻塞或冲突原因；已有候选链可从上方切换预览。';
    container.append(p);
    return null;
  }
  const svg=svgElement('svg',{xmlns:NS,viewBox:`0 0 ${model.width} ${model.height}`,
    width:model.width,height:model.height,role:'img','aria-label':model.mode==='network'?'选中链网，点击节点查看依据':'单条链预览，点击节点查看依据'});
  svg.append(svgElement('title',{},model.mode==='network'?'选中网络':'单条链预览'));
  svg.append(svgElement('desc',{},model.scope));
  svg.append(svgElement('rect',{width:'100%',height:'100%',fill:'#ffffff'}));
  const heading=model.mode==='network'?'选中网络':model.chains[0].selected?'选中网络内的单条链':'候选链预览（尚未被网络选中）';
  svg.append(svgElement('text',{x:22,y:26,'font-size':18,'font-family':'sans-serif',fill:'#243247'},heading));
  svg.append(svgElement('text',{x:22,y:model.height-12,'font-size':12,'font-family':'sans-serif',fill:'#526379'},'图示仅表达链网结构，不表示物理坐标或执行时间。'));
  const defs=svgElement('defs');svg.append(defs);
  model.chains.forEach((c,i)=>{
    const marker=svgElement('marker',{id:`net-arrow-${i}`,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});
    marker.append(svgElement('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:c.color}));defs.append(marker);
  });
  ['任务','目标','探知法器','协调法器','具现法器'].forEach((name,i)=>svg.append(svgElement('text',
    {x:122+i*246,y:62,'text-anchor':'middle','font-size':16,'font-family':'sans-serif',fill:'#526379'},name)));
  const nodes=new Map(model.nodes.map(n=>[n.key,n]));
  for(const e of model.edges) {
    const a=nodes.get(e.from),b=nodes.get(e.to),i=model.chains.findIndex(c=>c.id===e.chain);
    const offset=(i-(model.chains.length-1)/2)*10;
    const x1=a.x+200,y1=a.y+37+offset,x2=b.x,y2=b.y+37+offset;
    const bottom=model.returnY+i*28;
    const right=a.x+212+(i%6)*4,left=b.x-14-(i%6)*4;
    const d=e.kind==='return'?`M ${a.x+200} ${a.y+58} L ${right} ${a.y+58} L ${right} ${bottom} L ${left} ${bottom} L ${left} ${b.y+58} L ${b.x} ${b.y+58}`:
      `M ${x1} ${y1} C ${x1+22} ${y1}, ${x2-22} ${y2}, ${x2} ${y2}`;
    const path=svgElement('path',{d,fill:'none',stroke:e.color,'stroke-width':2.5,'stroke-dasharray':e.kind==='assignment'?'5 4':'none',
      'marker-end':`url(#net-arrow-${i})`,'data-chain':e.chain});
    path.append(svgElement('title',{},`${e.chain}：${a.label} → ${b.label}（${e.label}）`));svg.append(path);
    if(e.kind==='return') svg.append(svgElement('text',{x:665,y:bottom-5,fill:e.color,'font-size':12,'font-family':'sans-serif'},`${e.chain} · 具现至目标`));
  }
  for(const n of model.nodes) {
    const g=svgElement('g',{tabindex:0,role:'button','aria-label':`${LABELS[n.role]} ${n.label}，用于${n.chains.length}条链，查看依据`,style:'cursor:pointer'});
    g.append(svgElement('title',{},n.label));
    g.append(svgElement('rect',{x:n.x,y:n.y,width:200,height:74,rx:9,fill:n.chains.length>1?'#e5f2ed':'#f1f5fa',stroke:n.chains.length>1?'#197865':'#8293a8','stroke-width':1.5}));
    const text=svgElement('text',{x:n.x+12,y:n.y+23,'font-size':13,'font-family':'sans-serif',fill:'#243247'});
    const lines=labelLines(n.label);
    lines.forEach((line,i)=>text.append(svgElement('tspan',{x:n.x+12,dy:i?17:0},line)));
    g.append(text);
    g.append(svgElement('text',{x:n.x+12,y:n.y+64,'font-size':11,'font-family':'sans-serif',fill:'#526379'},
      n.chains.length>1?`共享于 ${n.chains.length} 条链`:LABELS[n.role]));
    const inspect=()=>onInspect(n,model.chains.filter(c=>n.chains.includes(c.id)));
    g.addEventListener('click',inspect);
    g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect();}});
    svg.append(g);
  }
  container.append(svg);
  return svg;
}

export function graphSvg(svg) {
  // Reparseable standalone SVG, with no scripts, external assets or HTML labels.
  return '<?xml version="1.0" encoding="UTF-8"?>\n'+new XMLSerializer().serializeToString(svg);
}
