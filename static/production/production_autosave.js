(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form||form.dataset.readonly==='1')return;
 const state=form.querySelector('[data-autosave-state]'),retry=form.querySelector('[data-autosave-retry]');
 const rows=()=>[...form.querySelectorAll('[data-production-person],[data-work-fact-row]')];
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),isPerson=row=>row.hasAttribute('data-production-person');
 const suffix=input=>input.name.replace(/^(works|labor)-\d+-/,'');
 const capture=row=>[...row.querySelectorAll('input,select,textarea')].filter(input=>input.name).map(input=>({name:input.name,value:input.value,checked:input.checked,type:input.type}));
 const fingerprint=row=>JSON.stringify(capture(row).filter(input=>!['id','person_claim_token','person_editing','work_claim_token','work_editing',...(isPerson(row)?['work_mode','work_targets','general','work','work_item']:[])].includes(input.name.replace(/^(works|labor)-\d+-/,''))).map(input=>[input.name.replace(/^(works|labor)-\d+-/,''),input.value,input.checked]));
 let timer=null,pending=false,running=null,failed=false,retryAttempt=0;
 const dirty=()=>rows().filter(row=>row.dataset.entryDirty||row.dataset.personDirty||row.dataset.autoPending);
 function message(text,error=false){state.textContent=text;state.classList.toggle('text-danger',error);retry.hidden=!error;}
 function rowMessage(row,text,error=false){const target=row.querySelector('[data-autosave-row-state]');if(target){target.textContent=text;target.title=text;target.classList.toggle('text-danger',error);}row.classList.toggle('production-person-auto-failed',error);}
 function prefix(row){return field(row,'id').name.replace(/-id$/,'');}
 function reindex(){
  for(const kind of ['works','labor']){
   const records=rows().filter(row=>prefix(row).startsWith(kind+'-'));
   records.sort((a,b)=>Number(!field(a,'id').value)-Number(!field(b,'id').value));
   records.forEach((row,index)=>{
    const before=prefix(row),after=kind+'-'+index;if(before===after)return;
    row.querySelectorAll('[name],[id],label[for]').forEach(node=>{for(const attribute of ['name','id','for']){const value=node.getAttribute(attribute);if(value?.includes(before+'-'))node.setAttribute(attribute,value.replace(before+'-',after+'-'));}});
    if(row.dataset.entryOriginal)row.dataset.entryOriginal=JSON.stringify(JSON.parse(row.dataset.entryOriginal).map(input=>({...input,name:input.name.replace(before+'-',after+'-')})));
   });
   form.elements.namedItem(kind+'-INITIAL_FORMS').value=records.filter(row=>field(row,'id').value).length;
   form.elements.namedItem(kind+'-TOTAL_FORMS').value=records.length;
  }
 }
 function schedule(row){
  if(row){delete row.dataset.autoRejected;row.dataset.autoPending='1';rowMessage(row,'Не сохранено');}
  pending=true;failed=false;retryAttempt=0;message('Есть изменения…');clearTimeout(timer);timer=setTimeout(()=>flush(),700);
 }
 async function save(){
  form.productionValidatePeople();
  const changed=dirty();
  for(const row of changed){
   if(isPerson(row)&&field(row,'employee').value){try{await form.productionClaimRow(row);}catch(error){if(field(row,'id').value||error.status!==409)throw error;row.dataset.autoRejected=error.message;field(row,'employee').value='';delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;row.dispatchEvent(new Event('change',{bubbles:true}));row.querySelector('[data-error]').textContent=error.message;rowMessage(row,error.message,true);}}
   else if(!isPerson(row)&&field(row,'project_work')?.value)await form.productionClaimRow(row);
  }
  const sent=new Map(rows().map(row=>[prefix(row),{row,stamp:fingerprint(row),capture:capture(row)}]));
  const body=new FormData(form);body.set('autosave','1');body.set('action','save');
  for(const row of rows()){
   const editing=field(row,isPerson(row)?'person_editing':'work_editing')?.value==='1';
   if(editing)for(const name of isPerson(row)?['employee','shift']:['project_work','work_item']){const input=field(row,name);if(input)body.set(input.name,input.value);}
  }
  changed.forEach(row=>rowMessage(row,'Сохранение…'));message('Сохранение…');
  const response=await fetch(form.getAttribute('action')||location.href,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body});
  if(!(response.headers.get('content-type')||'').includes('application/json')){const error=new Error('Не удалось сохранить изменения. Проверьте доступ к порталу и повторите сохранение.');error.retryable=response.status>=500||response.status===429;throw error;}
  const result=await response.json();
  if(!response.ok){
   for(const issue of result.errors||[]){const item=sent.get(issue.prefix);if(!item)continue;const text=issue.message||Object.values(issue.fields||{}).flat().join(' ');const target=item.row.querySelector('[data-error]');if(target)target.textContent=text;rowMessage(item.row,text,true);}
   const error=new Error(result.error||'Изменения не сохранены.');error.retryable=response.status>=500||response.status===429;throw error;
  }
  for(const name of ['production_snapshot','revision','labor_revision'])form.elements.namedItem(name).value=result[name];
  for(const entry of result.rows){
   const item=sent.get(entry.prefix);if(!item)continue;
   const row=item.row;
   if(entry.deleted){try{await form.productionReleaseRow(row);}catch{}row.remove();continue;}
   field(row,'id').value=entry.id;
   row.dataset.entryOriginal=JSON.stringify(item.capture.map(input=>input.name.endsWith('-id')?{...input,value:String(entry.id)}:input));
  }
  for(const item of sent.values()){
   const row=item.row;if(!row.isConnected)continue;
   if(fingerprint(row)===item.stamp){delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;row.dataset.autoSavedStamp=fingerprint(row);rowMessage(row,row.dataset.autoRejected|| (field(row,'id').value?'Сохранено':''),Boolean(row.dataset.autoRejected));if(!row.dataset.autoRejected)row.querySelector('[data-error]').textContent='';}
   else{row.dataset.autoPending='1';pending=true;rowMessage(row,'Есть изменения');}
  }
  reindex();form.dispatchEvent(new CustomEvent('production-report-update',{detail:result.report}));form.dispatchEvent(new Event('production-autosave-committed'));
  message(pending?'Есть изменения…':'Все изменения сохранены');
 }
 async function flush(){
  clearTimeout(timer);if(running)return running;
  running=(async()=>{
   while(pending||dirty().length){pending=false;try{await save();failed=false;retryAttempt=0;}catch(error){failed=true;dirty().forEach(row=>rowMessage(row,'Не сохранено',true));message(error.message,true);if((error.retryable||error instanceof TypeError)&&retryAttempt<3){retryAttempt++;clearTimeout(timer);timer=setTimeout(()=>{pending=true;flush();},1000*Math.pow(2,retryAttempt));}break;}}
   return !failed;
  })();
  try{return await running;}finally{running=null;}
 }
 window.productionAutoSave={flush,reindex,fingerprint,capture,get running(){return Boolean(running);},get pending(){return pending||Boolean(running)||dirty().length>0;}};
 const relevant=event=>{
  if(form.productionApplying)return;
  const row=event.target.closest('[data-production-person],[data-work-fact-row]');
  if(row){if(row.dataset.autoSavedStamp===fingerprint(row)&&!row.dataset.autoPending){delete row.dataset.entryDirty;delete row.dataset.personDirty;return;}if(isPerson(row)&&!field(row,'employee').value&&!field(row,'id').value)return;if(!isPerson(row)&&!field(row,'project_work')?.value)return;schedule(row);}
  else if(event.target.matches('[name=actuated],[name=actuation_type],[name=reason]'))schedule();
 };
 form.addEventListener('production-claims-change',()=>{if(!running&&!pending&&dirty().some(row=>field(row,isPerson(row)?'person_editing':'work_editing')?.value==='1'))schedule();});
 form.addEventListener('input',relevant);form.addEventListener('change',relevant);
 form.addEventListener('production-autosave-all',()=>{for(const row of rows())if(isPerson(row)&&field(row,'employee').value&&!field(row,'id').value&&field(row,'person_editing').value==='1'){row.dataset.entryDirty='1';schedule(row);}});
 rows().forEach(row=>{row.dataset.autoSavedStamp=fingerprint(row);if(field(row,'id').value)rowMessage(row,'Сохранено');});
 retry.addEventListener('click',()=>{retryAttempt=0;pending=true;flush();});
 form.addEventListener('click',async event=>{
  const button=event.target.closest('[data-person-remove],[data-work-remove]');if(!button)return;
  const row=button.closest('[data-production-person],[data-work-fact-row]');button.disabled=true;
  try{
   if(!field(row,'id').value&&!running){await form.productionReleaseRow(row);row.remove();reindex();form.dispatchEvent(new Event('change',{bubbles:true}));return;}
   await form.productionClaimRow(row);field(row,'DELETE').checked=true;row.classList.add('production-person-auto-deleting');row.dataset.entryDirty='1';row.dispatchEvent(new Event('change',{bubbles:true}));schedule(row);await flush();
  }catch(error){rowMessage(row,error.message,true);message(error.message,true);}finally{button.disabled=false;}
 });
 form.addEventListener('submit',event=>{event.preventDefault();flush();},true);
 window.addEventListener('beforeunload',event=>{if(running||pending||dirty().length){event.preventDefault();event.returnValue='';}});
})();
