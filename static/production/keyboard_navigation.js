(()=>{
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form)return;
 const undoStack=[],beforeEdit=new WeakMap();let replaying=false;
 const selector='input:not([type=hidden]),select,textarea,button,[tabindex]:not([tabindex="-1"])';
 const visible=node=>Boolean(node&&!node.disabled&&!node.hidden&&node.getClientRects().length&&!node.closest('[hidden]'));
 const reveal=node=>{for(let group=node?.closest('details');group;group=group.parentElement?.closest('details'))group.open=true;};
 const controls=row=>[...row.querySelectorAll(selector)].filter(visible);
 const focus=node=>{if(!node)return;reveal(node);node.focus({preventScroll:true});node.scrollIntoView({block:'nearest',inline:'nearest',behavior:'smooth'});};
 const suffix=node=>(node.name||node.dataset.keyboardField||'').replace(/^(works|labor)-\d+-/,'');
 const rowsFor=row=>row.matches('[data-work-fact-row]')?[...form.querySelectorAll('[data-work-fact-row]')].filter(item=>!item.hidden&&!item.closest('[hidden]')):[...form.querySelectorAll('[data-production-person]')].filter(visible);
 function siblingField(row,target,direction){const rows=rowsFor(row),next=rows[rows.indexOf(row)+direction];if(!next)return null;reveal(next);const name=suffix(target),same=controls(next).find(node=>suffix(node)===name);if(same)return same;const index=controls(row).indexOf(target);return controls(next)[Math.max(0,Math.min(index,controls(next).length-1))];}
 function textareaTarget(event,row){
  const target=event.target,key=event.key,value=target.value,start=target.selectionStart??0,end=target.selectionEnd??start,rows=rowsFor(row),index=rows.indexOf(row),quantity=item=>{if(!item)return null;reveal(item);return controls(item).find(node=>suffix(node)==='actual_quantity');};
  if(row.matches('[data-production-person]')){
   const list=controls(row),position=list.indexOf(target),next=rows[index+1];
   if(key==='ArrowLeft'&&start===0&&end===0)return list[position-1];
   if(event.ctrlKey&&key==='Enter'&&next){const comment=next.querySelector('[name$="-activity"]');if(comment){reveal(comment);return comment;}}
   if(key==='ArrowRight'&&start===value.length&&end===value.length&&next){reveal(next);return controls(next).find(node=>suffix(node)==='employee')||next.querySelector('[name$="-activity"]')||controls(next)[0];}
   if(key==='ArrowUp'||key==='ArrowDown'){const other=rows[index+(key==='ArrowUp'?-1:1)],comment=other?.querySelector('[name$="-activity"]');if(comment){reveal(comment);return comment;}}
   return null;
  }
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
  if(suffix(target)==='actual_quantity'&&key==='ArrowLeft'){const rows=rowsFor(row),previous=rows[rows.indexOf(row)-1];if(!previous)return false;reveal(previous);const comment=controls(previous).find(node=>suffix(node)==='comment');if(!comment)return false;event.preventDefault();focus(comment);return true;}
  if(target.matches('input[type=number]')&&event.ctrlKey&&['ArrowUp','ArrowDown'].includes(key)){event.preventDefault();key==='ArrowUp'?target.stepUp():target.stepDown();target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));return true;}
  let next=null;if(key==='ArrowLeft')next=list[index-1];else if(key==='ArrowRight'||key==='Enter')next=list[index+1];else if(key==='ArrowUp')next=siblingField(row,target,-1);else if(key==='ArrowDown')next=siblingField(row,target,1);else return false;
  if(!next)return false;event.preventDefault();focus(next);return true;
 }
 function crewRows(){return [...form.querySelectorAll('.production-crew-person')].filter(visible);}
 function moveCrew(event,row){const target=event.target,key=event.key,list=controls(row),index=list.indexOf(target);if(target.matches('button')&&key==='Enter')return false;let next=null;if(key==='ArrowLeft')next=list[index-1];else if(key==='ArrowRight'||key==='Enter')next=list[index+1];else if(['ArrowUp','ArrowDown'].includes(key)){const rows=crewRows(),other=rows[rows.indexOf(row)+(key==='ArrowUp'?-1:1)];if(other)next=controls(other)[Math.max(0,Math.min(index,controls(other).length-1))];}else return false;if(!next)return false;event.preventDefault();focus(next);return true;}
 function moveCrewForm(event,root){const target=event.target,key=event.key,list=controls(root),index=list.indexOf(target);if(index<0||target.matches('button')&&key==='Enter')return false;if(target.matches('input[type=text],input:not([type])')&&['ArrowLeft','ArrowRight'].includes(key)){const start=target.selectionStart??0,end=target.selectionEnd??start;if(key==='ArrowLeft'&&(start||end)||key==='ArrowRight'&&(start!==target.value.length||end!==target.value.length))return false;}let next=null;if(key==='ArrowLeft')next=list[index-1];else if(key==='ArrowRight'||key==='Enter')next=list[index+1];else if(['ArrowUp','ArrowDown'].includes(key)){const direction=key==='ArrowUp'?-1:1,same=target.matches('[data-crew-name],[data-crew-new-name]')?list.filter(node=>node.matches('[data-crew-name],[data-crew-new-name]')):list;next=same[same.indexOf(target)+direction]||list[index+direction];}else return false;if(!next)return false;event.preventDefault();focus(next);return true;}
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
  const crews=event.target.closest('[data-production-crews]');if(crews)return moveCrewForm(event,crews);
  const row=event.target.closest('[data-work-fact-row],[data-production-person]');if(row)moveInForm(event,row);
 },true);
 form.addEventListener('wheel',event=>{
  const input=event.target.closest('input[type=number]');if(!input||document.activeElement!==input)return;event.preventDefault();
  let scroller=input.parentElement;while(scroller&&scroller!==document.body){const style=getComputedStyle(scroller);if(scroller.scrollHeight>scroller.clientHeight&&/(auto|scroll)/.test(style.overflowY))break;scroller=scroller.parentElement;}
  if(scroller&&scroller!==document.body)scroller.scrollBy({top:event.deltaY,left:event.deltaX});else window.scrollBy({top:event.deltaY,left:event.deltaX});
 },{passive:false});
 const graphRoot=form.querySelector('[data-employee-graph]');if(graphRoot)new MutationObserver(wireGraph).observe(graphRoot,{childList:true,subtree:true});wireGraph();
 const stateOf=node=>node.type==='checkbox'||node.type==='radio'?Boolean(node.checked):node.value;
 const locate=node=>{const activity=node.closest('[data-crew-activity]')?.dataset.crewActivity;if(activity&&node.matches('[data-crew-assignment]'))return ()=>form.querySelector(`[data-crew-activity="${CSS.escape(activity)}"] [data-crew-assignment]`);const crew=node.closest('[data-crew-id]')?.dataset.crewId;if(crew&&node.matches('[data-crew-name]'))return ()=>form.querySelector(`[data-crew-id="${CSS.escape(crew)}"] [data-crew-name]`);const name=node.name;if(name)return ()=>form.elements.namedItem(name);return ()=>node.isConnected?node:null;};
 form.addEventListener('focusin',event=>{if(event.target.matches('input,select,textarea'))beforeEdit.set(event.target,stateOf(event.target));});
 const pushUndo=item=>{undoStack.push(item);if(undoStack.length>20)undoStack.shift();};
 form.addEventListener('change',event=>{const node=event.target;if(replaying||!node.matches('input,select,textarea')||!node.closest('[data-work-fact-row],[data-production-person]'))return;const before=beforeEdit.has(node)?beforeEdit.get(node):node.defaultValue,after=stateOf(node);beforeEdit.set(node,after);if(before===after)return;pushUndo({find:locate(node),value:before});});
 function rowSnapshot(row){const parent=row.parentNode,next=row.nextSibling,values=[...row.querySelectorAll('input,select,textarea')].map(node=>({name:node.name,type:node.type,value:node.value,checked:node.checked})),hidden=row.hidden,className=row.className;return ()=>{replaying=true;const target=row;if(!target.isConnected)parent.insertBefore(target,next?.isConnected?next:null);target.hidden=hidden;target.className=className;for(const saved of values){const matches=[...target.querySelectorAll(`[name="${CSS.escape(saved.name)}"]`)],node=['checkbox','radio'].includes(saved.type)?matches.find(item=>item.value===saved.value)||matches[0]:matches[0];if(!node)continue;if(['checkbox','radio'].includes(saved.type))node.checked=saved.checked;else node.value=saved.value;}delete target.dataset.entryDirty;delete target.dataset.personDirty;delete target.dataset.autoPending;target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));replaying=false;window.productionAutoSave?.reindex();focus(target.querySelector('[name$="-actual_quantity"],[name$="-employee"],input,select,textarea'));};}
 form.addEventListener('click',event=>{if(replaying||!event.target.closest('[data-work-clear],[data-work-remove],[data-person-remove]'))return;const row=event.target.closest('[data-work-fact-row],[data-production-person]');if(row)pushUndo({undo:rowSnapshot(row)});},true);
 form.addEventListener('production-autosave-committed',()=>{undoStack.length=0;});
 document.addEventListener('keydown',event=>{if(event.defaultPrevented||!(event.ctrlKey||event.metaKey)||event.altKey||event.shiftKey||event.key.toLocaleLowerCase()!=='z')return;if(form.querySelector('[data-entry-step="3"]:not([hidden])')&&form.productionCrewDraft?.undo()){event.preventDefault();return;}if(form.querySelector('[data-entry-step="4"]:not([hidden])'))return;if(event.target.matches('textarea,input[type=text],input[type=search],input:not([type])'))return;const item=undoStack.pop();if(!item)return;event.preventDefault();if(item.undo){item.undo();}else{const node=item.find();if(!node)return;replaying=true;if(node.type==='checkbox'||node.type==='radio')node.checked=Boolean(item.value);else node.value=item.value;beforeEdit.set(node,item.value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));replaying=false;focus(node);}const status=form.querySelector('[data-autosave-state]');if(status)status.textContent='Последнее действие отменено. Есть несохранённые изменения.';},true);
})();
