(()=>{
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form)return;
 const selector='input:not([type=hidden]),select,textarea,button,[tabindex]:not([tabindex="-1"])';
 const visible=node=>Boolean(node&&!node.disabled&&!node.hidden&&node.getClientRects().length&&!node.closest('[hidden]'));
 const controls=row=>[...row.querySelectorAll(selector)].filter(visible);
 const focus=node=>{if(!node)return;node.focus({preventScroll:true});node.scrollIntoView({block:'nearest',inline:'nearest',behavior:'smooth'});};
 const suffix=node=>(node.name||node.dataset.keyboardField||'').replace(/^(works|labor)-\d+-/,'');
 const rowsFor=row=>row.matches('[data-work-fact-row]')?[...form.querySelectorAll('[data-work-fact-row]')].filter(visible):[...form.querySelectorAll('[data-production-person]')].filter(visible);
 function siblingField(row,target,direction){const rows=rowsFor(row),next=rows[rows.indexOf(row)+direction];if(!next)return null;const name=suffix(target),same=controls(next).find(node=>suffix(node)===name);if(same)return same;const index=controls(row).indexOf(target);return controls(next)[Math.max(0,Math.min(index,controls(next).length-1))];}
 function textareaTarget(event,row){
  const target=event.target,key=event.key,value=target.value,start=target.selectionStart??0,end=target.selectionEnd??start,rows=rowsFor(row),index=rows.indexOf(row),quantity=item=>controls(item).find(node=>suffix(node)==='actual_quantity');
  if(event.ctrlKey&&key==='Enter')return quantity(rows[index+1]);
  if(key==='ArrowLeft'&&start===0&&end===0)return quantity(row);
  if(key==='ArrowRight'&&start===value.length&&end===value.length)return quantity(rows[index+1]);
  if(key==='ArrowUp'&&(value.indexOf('\n')<0||start<=value.indexOf('\n')))return siblingField(row,target,-1);
  if(key==='ArrowDown'&&(value.lastIndexOf('\n')<0||start>value.lastIndexOf('\n')))return siblingField(row,target,1);
  return null;
 }
 function moveInForm(event,row){
  const target=event.target,key=event.key,list=controls(row),index=list.indexOf(target);if(index<0)return false;
  if(target.matches('button')&&key==='Enter')return false;
  if(target.matches('textarea')){const next=textareaTarget(event,row);if(!next)return false;event.preventDefault();focus(next);return true;}
  if(target.matches('input[type=text],input[type=search],input:not([type])')&&['ArrowLeft','ArrowRight'].includes(key))return false;
  if(target.matches('input[type=number]')&&event.ctrlKey&&['ArrowUp','ArrowDown'].includes(key)){event.preventDefault();key==='ArrowUp'?target.stepUp():target.stepDown();target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));return true;}
  let next=null;if(key==='ArrowLeft')next=list[index-1];else if(key==='ArrowRight'||key==='Enter')next=list[index+1];else if(key==='ArrowUp')next=siblingField(row,target,-1);else if(key==='ArrowDown')next=siblingField(row,target,1);else return false;
  if(!next)return false;event.preventDefault();focus(next);return true;
 }
 function crewRows(){return [...form.querySelectorAll('.production-crew-person')].filter(visible);}
 function moveCrew(event,row){const target=event.target,key=event.key,list=controls(row),index=list.indexOf(target);if(target.matches('button')&&key==='Enter')return false;let next=null;if(key==='ArrowLeft')next=list[index-1];else if(key==='ArrowRight'||key==='Enter')next=list[index+1];else if(['ArrowUp','ArrowDown'].includes(key)){const rows=crewRows(),other=rows[rows.indexOf(row)+(key==='ArrowUp'?-1:1)];if(other)next=controls(other)[Math.max(0,Math.min(index,controls(other).length-1))];}else return false;if(!next)return false;event.preventDefault();focus(next);return true;}
 function graphCards(side){return [...form.querySelectorAll(side==='work'?'.graph-work':'.graph-person')].filter(visible);}
 function nearestAcross(card,side){const y=card.getBoundingClientRect().top+card.offsetHeight/2;return graphCards(side).sort((a,b)=>Math.abs(a.getBoundingClientRect().top+a.offsetHeight/2-y)-Math.abs(b.getBoundingClientRect().top+b.offsetHeight/2-y))[0];}
 function wireGraph(){
  form.querySelectorAll('.employee-graph-card').forEach(card=>{card.tabIndex=0;card.setAttribute('role','button');});
  form.querySelectorAll('.graph-edge-hit').forEach(edge=>{if(edge.dataset.keyboardReady)return;edge.dataset.keyboardReady='1';edge.setAttribute('role','button');edge.setAttribute('tabindex','0');edge.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();edge.dispatchEvent(new MouseEvent('click',{bubbles:true}));}else if(event.key==='Delete'||event.key==='Backspace'||event.key==='F10'&&event.shiftKey){event.preventDefault();edge.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:innerWidth/2,clientY:innerHeight/2}));}});});
 }
 function graphKey(event,card){
  const key=event.key,work=card.matches('.graph-work'),cards=graphCards(work?'work':'person'),index=cards.indexOf(card),details=card.closest('.employee-graph-crew');let next=null;
  if(key==='ArrowUp')next=cards[index-1];else if(key==='ArrowDown')next=cards[index+1];else if(key==='ArrowRight'){if(details&&!details.open){details.open=true;event.preventDefault();return true;}if(work)next=nearestAcross(card,'person');}else if(key==='ArrowLeft'){if(details&&details.open){details.open=false;event.preventDefault();return true;}if(!work)next=nearestAcross(card,'work');}else if(key==='Enter'||key===' '){event.preventDefault();card.click();return true;}else if(key==='F10'&&event.shiftKey){event.preventDefault();const box=card.getBoundingClientRect();card.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:box.left+20,clientY:box.top+20}));return true;}else return false;
  if(next){event.preventDefault();focus(next);return true;}return false;
 }
 form.addEventListener('keydown',event=>{
  if(event.defaultPrevented||event.altKey||event.metaKey||(event.ctrlKey&&event.key!=='Enter'&&!['ArrowUp','ArrowDown'].includes(event.key)))return;
  const graph=event.target.closest('.employee-graph-card');if(graph&&event.target===graph)return graphKey(event,graph);
  const toggle=event.target.closest('[data-crew-card-toggle]');if(toggle&&['ArrowLeft','ArrowRight','Enter'].includes(event.key)){const expanded=toggle.getAttribute('aria-expanded')==='true',wanted=event.key==='ArrowRight'||event.key==='Enter'&&!expanded;if(wanted!==expanded)toggle.click();event.preventDefault();return;}
  const crew=event.target.closest('.production-crew-person');if(crew)return moveCrew(event,crew);
  const row=event.target.closest('[data-work-fact-row],[data-production-person]');if(row)moveInForm(event,row);
 },true);
 const graphRoot=form.querySelector('[data-employee-graph]');if(graphRoot)new MutationObserver(wireGraph).observe(graphRoot,{childList:true,subtree:true});wireGraph();
})();
