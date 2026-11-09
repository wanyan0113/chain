import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {buildGraph,buildReferenceGraph} from '../design/network_view.mjs';

const report=JSON.parse(fs.readFileSync(process.env.NETWORK_REPORT,'utf8'));

test('ordered trajectories render all repeated-role stages with correct roles',()=>{
  const doc=structuredClone(report),chain=doc.tasks[0].chains[0];
  const roles=['insight','coordination','manifestation','coordination','manifestation','insight'];
  const ids=['eye-1','center-1','effect-1','center-2','effect-2','eye-2'];
  chain.plan={mode:'ordered_steps',steps:roles.map((role,i)=>({id:`s${i}`,role,artifact:ids[i]})),
    connections:roles.slice(1).map(()=>({relation:'communication'}))};
  chain.artifacts=Object.fromEntries(ids.map((id,i)=>[`s${i}`,id]));
  chain.link_evidence=ids.slice(1).map((to,i)=>({from:ids[i],to}));
  const graph=buildGraph(doc,chain.id);
  assert.equal(graph.nodes.length,8);
  assert.equal(graph.edges.length,7);
  assert.deepEqual(graph.nodes.filter(n=>n.kind==='artifact').map(n=>n.role),roles);
  assert.deepEqual(graph.nodes.filter(n=>n.kind==='artifact').map(n=>n.label),ids);
  assert.equal(graph.edges.filter(e=>e.kind==='cooperation').length,5);
  assert.ok(graph.width>1112);
});

test('selected network reuses shared resources, keeps only chosen chains and directed evidence',()=>{
  const before=structuredClone(report),g=buildGraph(report);
  assert.equal(g.chains.length,2);
  assert.equal(g.nodes.length,8); // four target occurrences and four distinct artifacts
  assert.equal(g.nodes.filter(n=>n.kind==='task').length,0);
  for(const id of ['demo-eye','demo-coordinator']) {
    const nodes=g.nodes.filter(n=>n.kind==='artifact'&&n.label===id);
    assert.equal(nodes.length,1);assert.equal(nodes[0].chains.length,2);
  }
  assert.deepEqual(g.chains.map(c=>c.id),report.network.chains);
  assert.equal(g.edges.length,7); // the common insight->coordination edge is drawn once
  assert.equal(g.nodes.filter(n=>n.kind==='target'&&n.column===0).length,2);
  assert.equal(g.nodes.filter(n=>n.kind==='target'&&n.column===4).length,2);
  assert.equal(g.edges.find(e=>e.chains.length===2).evidence.length,2);
  const ids=new Map(g.nodes.map(n=>[n.key,n]));
  for(const edge of g.edges.filter(e=>e.kind==='cooperation')) {
    for(const evidence of edge.evidence) {
      assert.equal(evidence.link.from,ids.get(edge.from).label);
      assert.equal(evidence.link.to,ids.get(edge.to).label);
    }
    assert.ok(ids.get(edge.from).column<ids.get(edge.to).column);
  }
  assert.deepEqual(g.relations,report.network.relations);
  assert.deepEqual(report,before);
});

test('unselected candidates remain previews, and blocked reports have no selected network',()=>{
  const chain=report.tasks.flatMap(t=>t.chains).find(c=>!report.network.chains.includes(c.id));
  const single=buildGraph(report,chain.id);
  assert.equal(single.mode,'chain');assert.equal(single.chains.length,1);
  assert.equal(single.chains[0].selected,false);assert.deepEqual(single.relations,[]);
  const blocked=structuredClone(report);blocked.status='blocked';blocked.network=null;
  assert.deepEqual(buildGraph(blocked).nodes,[]);
  assert.equal(buildGraph(blocked,chain.id).chains[0].selected,false);
});

test('targets have separate left/right occurrences of one identity, and never merge with artifacts',()=>{
  const doc=structuredClone(report);
  doc.tasks[0].task='demo-eye';doc.tasks[0].target='demo-eye';
  const g=buildGraph(doc,doc.tasks[0].chains[0].id);
  assert.equal(g.nodes.filter(n=>n.label==='demo-eye').length,3);
  assert.equal(new Set(g.nodes.map(n=>n.key)).size,g.nodes.length);
  const targets=g.nodes.filter(n=>n.kind==='target');
  assert.equal(targets[0].objectKey,targets[1].objectKey);
  assert.notEqual(targets[0].key,targets[1].key);
});

test('all candidates form separate five-node horizontal rows, including unselected chains',()=>{
  const g=buildGraph(report,null,{allCandidates:true});
  assert.equal(g.mode,'candidates');assert.equal(g.chains.length,4);
  assert.equal(g.nodes.length,20);assert.equal(g.edges.length,16);
  for(const c of g.chains) {
    const nodes=g.nodes.filter(n=>n.chains.includes(c.id));
    assert.equal(nodes.length,5);
    assert.equal(new Set(nodes.map(n=>n.y)).size,1);
    assert.deepEqual(nodes.map(n=>n.column),[0,1,2,3,4]);
    assert.equal(nodes[0].objectKey,nodes[4].objectKey);
  }
  assert.equal(g.chains.filter(c=>c.selected).length,2);
  assert.equal(new Set(g.chains.map(c=>g.nodes.find(n=>n.chains.includes(c.id)).y)).size,4);
});

test('invalid report chain references are rejected instead of showing partial success',()=>{
  assert.throws(()=>buildGraph(report,'absent'),/无法唯一定位/);
  const bad=structuredClone(report);bad.network.chains.push('absent');
  assert.throws(()=>buildGraph(bad),/无法唯一定位/);
});

test('node evidence belongs to the chain task and retains rule and link sources',()=>{
  const g=buildGraph(report);
  for(const c of g.chains) {
    const task=report.tasks.find(t=>t.task===c.task);
    const chain=task.chains.find(ch=>ch.id===c.id);
    assert.deepEqual(c.evidence.task_rules,task.inference);
    assert.deepEqual(c.evidence.links,chain.link_evidence);
    assert.deepEqual(c.evidence.artifacts.map(a=>a.artifact).sort(),Object.values(chain.artifacts).sort());
  }
});

test('article structure remains a reported reference, never an independently selected network',()=>{
  const reference=JSON.parse(fs.readFileSync(process.env.NETWORK_REFERENCE,'utf8'));
  const before=structuredClone(reference);
  const chain=buildReferenceGraph(reference);
  assert.equal(chain.mode,'reference-chain');
  assert.equal(chain.chains.length,1);
  assert.ok(chain.nodes.some(n=>n.label==='R-Sigil-B'&&n.platform==='R-Gryphon#1'));
  const network=buildReferenceGraph(reference,{network:true});
  assert.equal(network.mode,'reference-network');
  assert.equal(network.status,'reference_reproduced');
  assert.ok(network.title.includes('未独立验收'));
  assert.equal(network.nodes.filter(n=>n.label==='R-Eye-A').length,1);
  assert.equal(network.nodes.filter(n=>n.label==='联合统筹中枢').length,1);
  assert.equal(network.chains.filter(c=>c.selected).length,0);
  assert.equal(network.edges.filter(e=>e.chains.length===2).length,1);
  assert.equal(network.chains[1].metrics,null);
  assert.equal(network.chains[1].score,null);
  for(const e of network.edges) for(const evidence of e.evidence) {
    assert.equal(evidence.independent_feasibility,'not_verified');
    assert.ok(evidence.source_refs.length>0);
    assert.equal(evidence.link,undefined);
  }
  assert.deepEqual(reference,before);
});
