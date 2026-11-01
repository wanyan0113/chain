import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {buildGraph} from '../design/network_view.mjs';

const report=JSON.parse(fs.readFileSync(process.env.NETWORK_REPORT,'utf8'));

test('selected network reuses shared resources, keeps only chosen chains and directed evidence',()=>{
  const before=structuredClone(report),g=buildGraph(report);
  assert.equal(g.chains.length,2);
  assert.equal(g.nodes.length,8); // two missions, two targets, four distinct artifacts
  for(const id of ['demo-eye','demo-coordinator']) {
    const nodes=g.nodes.filter(n=>n.kind==='artifact'&&n.label===id);
    assert.equal(nodes.length,1);assert.equal(nodes[0].chains.length,2);
  }
  assert.deepEqual(g.chains.map(c=>c.id),report.network.chains);
  assert.equal(g.edges.filter(e=>e.kind==='return').length,2);
  const ids=new Map(g.nodes.map(n=>[n.key,n]));
  for(const edge of g.edges.filter(e=>e.kind==='cooperation')) {
    assert.equal(edge.evidence.from,ids.get(edge.from).label);
    assert.equal(edge.evidence.to,ids.get(edge.to).label);
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

test('task, target and artifact IDs occupy separate identities',()=>{
  const doc=structuredClone(report);
  doc.tasks[0].task='demo-eye';doc.tasks[0].target='demo-eye';
  const g=buildGraph(doc,doc.tasks[0].chains[0].id);
  assert.equal(g.nodes.filter(n=>n.label==='demo-eye').length,3);
  assert.equal(new Set(g.nodes.map(n=>n.key)).size,g.nodes.length);
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
