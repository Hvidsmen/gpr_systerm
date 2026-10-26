const test=require('node:test'),assert=require('node:assert/strict');
const {arrange,crossings}=require('../static/planning/resource_graph_layout.js');
const works=ids=>ids.map(token=>({token}));
const resource=(id,...workTokens)=>({id,workTokens});

test('oppositely ordered pairs align automatically, without duplicate resources',()=>{
 const input=[resource('r3','c'),resource('r2','b'),resource('r1','a')];
 const layout=arrange(works(['a','b','c']),input);
 assert.deepEqual(layout.components.map(c=>c.resources.map(r=>r.id)),[['r1'],['r2'],['r3']]);
 assert.deepEqual(input.map(r=>r.id),['r3','r2','r1']);
});
test('shared equipment connects work branches into one component and is counted once',()=>{
 const layout=arrange(works(['object:1/work:1','object:2/work:2','free']),[resource('shared','object:1/work:1','object:2/work:2'),resource('crew','object:2/work:2')]);
 assert.equal(layout.components.length,1);
 assert.equal(layout.components[0].works.length,2);
 assert.equal(layout.components[0].resources.filter(r=>r.id==='shared').length,1);
 assert.deepEqual(layout.unlinkedWorks.map(w=>w.token),['free']);
});
test('object resources and links hidden by filtering remain distinct',()=>{
 const layout=arrange(works(['visible']),[resource('outside','hidden'),resource('object'),resource('mixed','hidden','visible')]);
 assert.deepEqual(layout.hiddenResources.map(r=>r.id),['outside']);
 assert.deepEqual(layout.unlinkedResources.map(r=>r.id),['object']);
 assert.deepEqual(layout.components[0].resources[0].workTokens,['hidden','visible']);
});
test('crossing counter ignores shared endpoints and matches pairwise definition',()=>{
 const neighbors=new Map([['a',new Set(['z','x'])],['b',new Set(['y','x'])]]);
 assert.equal(crossings(['a','b'],['x','y','z'],neighbors),2);
});
test('layout is deterministic and never increases crossings within dense components',()=>{
 let seed=123;const random=()=>{seed=(seed*16807)%2147483647;return seed/2147483647;};
 for(let trial=0;trial<60;trial++){
  const left=works(Array.from({length:12},(_,i)=>'w'+i));
  const right=Array.from({length:15},(_,i)=>resource('r'+i,...left.filter(()=>random()<.2).map(w=>w.token)));
  const before=JSON.stringify([left,right]),layout=arrange(left,right);
  assert.deepEqual(layout,arrange(left,right));assert.equal(JSON.stringify([left,right]),before);
  const byWork=new Map(left.map(w=>[w.token,new Set(right.filter(r=>r.workTokens.includes(w.token)).map(r=>r.id))]));
  for(const component of layout.components){
   const oldLeft=left.filter(w=>component.works.includes(w)).map(w=>w.token),oldRight=right.filter(r=>component.resources.includes(r)).map(r=>r.id);
   assert(crossings(component.works.map(w=>w.token),component.resources.map(r=>r.id),byWork)<=crossings(oldLeft,oldRight,byWork));
  }
  const ids=[...layout.components.flatMap(c=>c.resources),...layout.unlinkedResources,...layout.hiddenResources].map(r=>r.id);
  assert.equal(new Set(ids).size,right.length);assert.equal(ids.length,right.length);
 }
});
test('dense shared groups stay bounded and duplicate/hidden targets are safe',()=>{
 const left=works(Array.from({length:120},(_,i)=>'w'+i));
 const right=Array.from({length:160},(_,i)=>resource('r'+i,...left.filter((_,j)=>(i+j)%3===0).map(w=>w.token),'hidden','hidden'));
 const layout=arrange(left,right);assert.equal(layout.components.length,3);
 assert.equal(layout.components.reduce((sum,c)=>sum+c.resources.length,0),160);
});
test('empty and filtered graphs keep all unconnected cards',()=>{
 assert.deepEqual(arrange([],[]),{components:[],unlinkedWorks:[],hiddenResources:[],unlinkedResources:[]});
 assert.equal(arrange([],[resource('r','hidden')]).hiddenResources.length,1);
 assert.equal(arrange(works(['a']),[]).unlinkedWorks.length,1);
});
