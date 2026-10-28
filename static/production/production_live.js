/* One session per production tab. Relationship writes never take data leases. */
(()=>{
 'use strict';const form=document.querySelector('[data-production-form]');if(!form||form.dataset.readonly==='1')return;
 const {scope,csrf}=form.productionSession,field=(row,name)=>row.querySelector(`[name$="-${name}"]`),rows=()=>[...form.querySelectorAll('[data-work-fact-row],[data-production-person]')];
 let step=Number(form.dataset.initialStep||1),digest='',polling=false,closed=false;
 const snapshot=()=>form.elements.namedItem('production_snapshot').value;
 function setValues(row,values,relationsOnly=false){
  const relationNames=new Set(['work','work_item','general','work_mode','work_targets']);
  for(const [name,value] of Object.entries(values)){
   if(relationsOnly&&!relationNames.has(name))continue;
   if(name==='work_targets'){
    row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());
    for(const target of value){const input=document.createElement('input');input.type='hidden';input.name=field(row,'work_mode').name.replace(/work_mode$/,'work_targets');input.value=target;row.append(input);}continue;
   }
   const input=field(row,name);if(!input)continue;
   if(input.tagName==='SELECT'&&value&&!Array.from(input.options).some(o=>o.value===String(value)))input.add(new Option(String(value),String(value)));
   if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value??'';
  }
 }
 function merge(report){
  // Always refresh relationships, including on a person reserved by this user.
  for(const person of report.people){const row=rows().find(r=>r.matches('[data-production-person]')&&String(field(r,'id')?.value)===String(person.id));if(!row)continue;
   row.dataset.linksRevision=person.links_revision;
   setValues(row,{work_targets:person.targets,work_mode:person.general?'general':person.reviewed?'works':'pending',general:person.general},true);
  }
  document.getElementById('production-graph-saved').textContent=JSON.stringify(report.works);
  form.dispatchEvent(new CustomEvent('production-report-update',{detail:report}));
 }
 function apply(data){
  const keep=new Set(rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').map(form.productionKeyOf));
  form.productionApplying=true;
  try{
   for(const kind of ['works','labor']){
    const canonical=data.rows[kind],host=form.querySelector(`[data-rows="${kind}"]`),selector=kind==='works'?'[data-work-fact-row]':'[data-production-person]',existing=[...host.querySelectorAll(selector)],ids=new Set(canonical.map(r=>String(r.id)));
    for(const row of existing){if(field(row,'id')?.value&&!ids.has(field(row,'id').value)&&!keep.has(form.productionKeyOf(row)))row.remove();}
    for(const record of canonical){
     if(keep.has(record.key))continue;
     let row=[...host.querySelectorAll(selector)].find(r=>field(r,'id')?.value===String(record.id)||form.productionKeyOf(r)===record.key);
     if(!row){const index=Number(form.elements.namedItem(kind+'-TOTAL_FORMS').value);host.insertAdjacentHTML('beforeend',document.getElementById('empty-'+kind).innerHTML.replaceAll('__prefix__',index));row=host.lastElementChild;form.elements.namedItem(kind+'-TOTAL_FORMS').value=index+1;}
     setValues(row,record.values);
     delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;
     row.dataset.entryOriginal=JSON.stringify(window.productionAutoSave.capture(row));row.dataset.autoSavedStamp=window.productionAutoSave.fingerprint(row);
    }
   }
   window.productionAutoSave.reindex();form.elements.namedItem('production_snapshot').value=data.production_snapshot;
   merge(data.report);form.dispatchEvent(new Event('production-live-applied'));form.productionSetClaims(data.claims);
  }finally{form.productionApplying=false;}
 }
 function presence(users){const host=form.querySelector('[data-production-presence]');if(!host)return;host.replaceChildren();for(const user of users){const chip=document.createElement('span');chip.className='production-presence-user';chip.textContent=user.name+(user.self?' · Вы':'')+' · '+user.steps.map(s=>['','Работы','Люди','Связи','Отчёт'][s]).join(', ')+' · '+(user.editing?'редактирует':'смотрит');host.append(chip);}}
 async function poll(){
  if(polling||closed)return;polling=true;const original=snapshot(),saving=window.productionAutoSave.running;
  const keep=rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').map(form.productionKeyOf);
  try{
   const response=await fetch(form.dataset.liveUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...scope,step,digest,keep,production_snapshot:original})});if(!response.ok)return;
   const data=await response.json();presence(data.users);
   const currentKeep=rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').map(form.productionKeyOf);
   if(saving||window.productionAutoSave.running||original!==snapshot()||JSON.stringify(keep)!==JSON.stringify(currentKeep))return;
   if(data.rows&&data.production_snapshot)apply(data);else form.productionSetClaims(data.claims);
   digest=data.digest;
  }catch{/* Keep local input; the next heartbeat retries. */}finally{polling=false;}
 }
 form.productionChangeLinks=async(row,operation)=>{
  if(!field(row,'id')?.value){if(!await window.productionAutoSave.flush())throw new Error('Сначала исправьте ошибки сохранения сотрудника.');}
  const response=await fetch(form.dataset.linksUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...scope,activity:field(row,'id').value,revision:Number(row.dataset.linksRevision||0),...operation})});
  const result=await response.json();if(result.report)merge(result.report);digest='';
  if(!response.ok)throw new Error(result.error||'Не удалось изменить связь.');
  poll();
 };
 form.addEventListener('production-step-change',event=>{step=event.detail;poll();});
 form.addEventListener('production-autosave-committed',()=>{digest='';poll();});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
 window.addEventListener('pagehide',()=>{closed=true;navigator.sendBeacon(form.dataset.liveUrl,new URLSearchParams({...scope,action:'close',csrfmiddlewaretoken:csrf}));});
 window.addEventListener('pageshow',()=>{closed=false;digest='';poll();});
 setInterval(poll,1500);poll();
})();
