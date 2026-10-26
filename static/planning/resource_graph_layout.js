/* Deterministic bipartite layout. Changes display order, never assignments. */
(() => {
 'use strict';
 const positions=items=>new Map(items.map((id,index)=>[id,index]));

 function crossings(works,resources,neighbors){
  const rank=positions(resources),tree=new Uint32Array(resources.length+1);
  const prefix=i=>{let sum=0;for(;i>0;i-=i&-i)sum+=tree[i];return sum;};
  let total=0,seen=0;
  for(const work of works){
   const indices=[...(neighbors.get(work)||[])].filter(id=>rank.has(id)).map(id=>rank.get(id)+1);
   // Edges sharing a work or a resource do not cross each other.
   for(const index of indices)total+=seen-prefix(index);
   for(const index of indices){for(let i=index;i<tree.length;i+=i&-i)tree[i]++;seen++;}
  }
  return total;
 }

 function orderComponent(works,resources,byWork,byResource){
  let left=[...works],right=[...resources],best={works:left,resources:right};
  let score=crossings(left,right,byWork);
  function sweep(items,opposite,neighbors){
   const rank=positions(opposite),previous=positions(items);
   const center=new Map(items.map(id=>{const links=[...neighbors.get(id)].filter(key=>rank.has(key));return [id,links.reduce((sum,key)=>sum+rank.get(key),0)/links.length];}));
   return [...items].sort((a,b)=>center.get(a)-center.get(b)||previous.get(a)-previous.get(b));
  }
  // Bounded barycentre sweeps keep dense many-to-many groups responsive.
  for(let pass=0;pass<8;pass++){
   if(pass%2){left=sweep(left,right,byWork);right=sweep(right,left,byResource);}
   else{right=sweep(right,left,byResource);left=sweep(left,right,byWork);}
   const next=crossings(left,right,byWork);
   if(next<score){score=next;best={works:left,resources:right};}
   if(score===0)break;
  }
  return best;
 }

 function arrange(works,resources){
  const workMap=new Map(works.map(work=>[work.token,work]));
  const resourceMap=new Map(resources.map(resource=>[String(resource.id),resource]));
  const byWork=new Map(works.map(work=>[work.token,new Set()])),byResource=new Map();
  for(const resource of resources){
   const id=String(resource.id),targets=new Set(resource.workTokens.filter(key=>workMap.has(key)));
   byResource.set(id,targets);for(const key of targets)byWork.get(key).add(id);
  }
  const seenWorks=new Set(),seenResources=new Set(),components=[];
  for(const work of works){
   if(seenWorks.has(work.token)||!byWork.get(work.token).size)continue;
   const left=[],right=[],queue=[work.token];seenWorks.add(work.token);
   for(let i=0;i<queue.length;i++){
    const key=queue[i];left.push(key);
    for(const id of byWork.get(key)){
     if(seenResources.has(id))continue;
     seenResources.add(id);right.push(id);
     for(const neighbor of byResource.get(id))if(!seenWorks.has(neighbor)){seenWorks.add(neighbor);queue.push(neighbor);}
    }
   }
   // Seed in catalogue order so reloads and repeated filtering are stable.
   const members=new Set(left),membersRight=new Set(right);
   const ordered=orderComponent(works.filter(w=>members.has(w.token)).map(w=>w.token),resources.filter(r=>membersRight.has(String(r.id))).map(r=>String(r.id)),byWork,byResource);
   components.push({key:work.token,works:ordered.works.map(key=>workMap.get(key)),resources:ordered.resources.map(id=>resourceMap.get(id))});
  }
  return {components,unlinkedWorks:works.filter(work=>!seenWorks.has(work.token)),
   hiddenResources:resources.filter(resource=>!seenResources.has(String(resource.id))&&resource.workTokens.length),
   unlinkedResources:resources.filter(resource=>!resource.workTokens.length)};
 }
 const api={arrange,crossings};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(typeof window!=='undefined')window.resourceGraphLayout=api;
})();
