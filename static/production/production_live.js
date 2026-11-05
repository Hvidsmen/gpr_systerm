/* One session per production tab. Relationship writes never take data leases. */
(()=>{
 'use strict';const form=document.querySelector('[data-production-form]');if(!form||form.dataset.readonly==='1')return;
 const {scope,csrf}=form.productionSession,field=(row,name)=>row.querySelector(`[name$="-${name}"]`),rows=()=>[...form.querySelectorAll('[data-work-fact-row],[data-production-person]')];
 let reasonOptions=new Map();
 let step=Number(form.dataset.initialStep||1),digest='',claimsDigest='',usersDigest='',polling=false,closed=false,timer=null,failures=0,slow=false,lastActivity=Date.now(),lastSync=0,published='';
 const knownSnapshots=new Set();let outbox=null;
 let canonical={works:JSON.parse(document.getElementById('production-graph-saved').textContent),people:JSON.parse(document.getElementById('production-report-people').textContent)};
 const snapshot=()=>form.elements.namedItem('production_snapshot').value;
 function setValues(row,values,relationsOnly=false){
  const relationNames=new Set(['work','work_item','general','work_mode','work_targets']);
  for(const [name,value] of Object.entries(values)){
   if(relationsOnly&&!relationNames.has(name))continue;
   if(name==='work_targets'){
    if(JSON.stringify([...row.querySelectorAll('[name$="-work_targets"]')].map(i=>i.value))===JSON.stringify(value))continue;
    row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());
    for(const target of value){const input=document.createElement('input');input.type='hidden';input.name=field(row,'work_mode').name.replace(/work_mode$/,'work_targets');input.value=target;row.append(input);}continue;
   }
   if(name==='actuation_reasons'){
    const selected=(value||[]).map(String),host=row.querySelector('[data-actuation-reasons]');
    if(host)for(const [id,option] of reasonOptions){let input=[...row.querySelectorAll('input[name$="-actuation_reasons"]')].find(i=>i.value===id);if(!input&&(option.active||selected.includes(id))){const label=document.createElement('label');input=document.createElement('input');input.type='checkbox';input.value=id;input.name=field(row,'actuation_reasons_present').name.replace(/actuation_reasons_present$/,'actuation_reasons');label.append(input,document.createTextNode(option.label));host.append(label);}if(input){input.closest('label').replaceChildren(input,document.createTextNode(option.label));input.disabled=!option.active&&!selected.includes(id);}}
    row.querySelectorAll('input[name$="-actuation_reasons"]').forEach(input=>input.checked=selected.includes(input.value));continue;
   }
   const input=field(row,name);if(!input)continue;
   if(input.tagName==='SELECT'&&value&&!Array.from(input.options).some(o=>o.value===String(value)))input.add(new Option(String(value),String(value)));
   if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value??'';
  }
 }
 function publish(){
  const allowed=new Set(canonical.works.filter(w=>Number(w.actual_quantity)>0).map(w=>w.work_item_id?`item:${w.project_work_id}:${w.work_item_id}`:`work:${w.project_work_id}`));
  const clean={...canonical,people:canonical.people.map(p=>({...p,targets:p.targets.filter(t=>allowed.has(t))}))};
  const report=outbox?outbox.overlay(clean):clean,stamp=JSON.stringify(report);if(stamp===published)return;published=stamp;
  const byId=new Map(rows().filter(r=>r.matches('[data-production-person]')).map(r=>[Number(field(r,'id')?.value),r]));
  for(const person of report.people){const row=byId.get(Number(person.id));if(!row)continue;
   row.dataset.linksRevision=person.links_revision;row.dataset.pendingLinks=person.pending_links||0;
   setValues(row,{work_targets:person.targets,work_mode:person.general?'general':person.reviewed?'works':'pending',general:person.general},true);
  }
  document.getElementById('production-graph-saved').textContent=JSON.stringify(report.works);
  form.dispatchEvent(new CustomEvent('production-report-update',{detail:report}));
 }
 function merge(report){canonical=report;digest='';publish();}
 form.productionApplyReport=merge;
 outbox=new window.ProductionLinksOutbox(form,{digest:()=>digest,ackDigest:value=>{digest=value;},changed:()=>{lastActivity=Date.now();publish();},people:(people,removed)=>{
  const byId=new Map(canonical.people.map(p=>[Number(p.id),p]));for(const person of people)if(byId.has(Number(person.id)))byId.set(Number(person.id),{...byId.get(Number(person.id)),...person});for(const id of removed)byId.delete(Number(id));canonical={...canonical,people:[...byId.values()]};
  published='';
 }});
 form.productionLinks=outbox;
 function apply(data){
  const keep=new Set(rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').flatMap(form.productionKeysOf));
  form.productionApplying=true;
  reasonOptions=new Map((data.reason_options||[]).map(r=>[String(r.id),r]));
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
   merge(data.report);form.dispatchEvent(new Event('production-live-applied'));if(data.claims)form.productionSetClaims(data.claims);
  }finally{form.productionApplying=false;}
 }
 let presenceStamp='';
 function presence(users){const stamp=JSON.stringify(users);if(stamp===presenceStamp)return;presenceStamp=stamp;const host=form.querySelector('[data-production-presence]');if(!host)return;host.replaceChildren();for(const user of users){const chip=document.createElement('span');chip.className='production-presence-user';chip.textContent=user.name+(user.self?' · Вы':'')+' · '+user.steps.map(s=>['','Работы','Люди','Связи','Отчёт','План / факт СМГ'][s]).join(', ')+' · '+(user.editing?'редактирует':'смотрит');host.append(chip);}}
 const wait=()=>failures?Math.min(60000,3000*2**failures):slow?12000:Date.now()-lastActivity>45000?15000:3000;
 function schedule(delay=wait()){clearTimeout(timer);if(!closed)timer=setTimeout(()=>poll(),delay);}
 async function poll(force=false){
  if(polling||closed||document.hidden||navigator.onLine===false||outbox.busy||form.dataset.peopleBulkBusy==='1'||form.dataset.personShiftBusy==='1'||window.productionAutoSave.running){schedule();return false;}
  if(!force&&lastSync&&Date.now()-lastSync<2500){schedule();return false;}
  polling=true;const original=snapshot(),saving=window.productionAutoSave.running,started=performance.now();
  const keep=rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').flatMap(form.productionKeysOf);
  const snapshotKey=original.split(':').at(-1),body={...scope,protocol:'compact-v1',step,digest,claims_digest:claimsDigest,users_digest:usersDigest,keep,snapshot_key:snapshotKey};
  if(!knownSnapshots.has(snapshotKey))body.production_snapshot=original;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
  try{
   const response=await fetch(form.dataset.liveUrl,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(body)});
   if(response.status===409){const result=await response.json();if(result.reset_required){closed=true;form.dataset.resetRequired='1';const warning=document.createElement('div');warning.className='alert alert-warning';warning.role='alert';warning.append(document.createTextNode(result.error+' '));const link=document.createElement('a');link.href=location.href;link.textContent='Обновить форму';warning.append(link);form.before(warning);}return false;}
   if(response.status===403&&response.headers.get('content-type')?.includes('application/json')){const result=await response.json();if(result.auth_changed){closed=true;form.dataset.authChanged='1';form.querySelector('[data-links-status]').textContent=result.error;}return false;}
   if(response.redirected||response.status===401||response.status===403){closed=true;form.dataset.authChanged='1';form.querySelector('[data-links-status]').textContent='Проверьте вход и права в портале. Изменения связей сохранены в этом браузере.';return false;}
   if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('sync');
   const data=await response.json();failures=0;slow=performance.now()-started>800;lastSync=Date.now();
   if(data.need_snapshot){knownSnapshots.delete(snapshotKey);return false;}knownSnapshots.add(snapshotKey);
   if(data.users)presence(data.users);
   const currentKeep=rows().filter(r=>r.dataset.entryDirty||r.dataset.personDirty||r.dataset.autoPending||field(r,r.matches('[data-production-person]')?'person_editing':'work_editing')?.value==='1').flatMap(form.productionKeysOf);
   if(outbox.busy||form.dataset.peopleBulkBusy==='1'||form.dataset.personShiftBusy==='1'||saving||window.productionAutoSave.running||original!==snapshot()||JSON.stringify(keep)!==JSON.stringify(currentKeep))return false;
   if(data.rows&&data.production_snapshot){apply(data);knownSnapshots.add(data.snapshot_key);}else if(data.claims)form.productionSetClaims(data.claims);
   if(data.digest)digest=data.digest;if(data.claims_digest)claimsDigest=data.claims_digest;if(data.users_digest)usersDigest=data.users_digest;
   return true;
  }catch{failures++;slow=true;return false;}finally{clearTimeout(timeout);polling=false;schedule();}
 }
 form.productionRefresh=()=>poll(true);
 form.productionChangeLinks=async(row,operation)=>{
  if(!field(row,'id')?.value){if(!await window.productionAutoSave.flush())throw new Error('Сначала исправьте ошибки сохранения сотрудника.');}
  lastActivity=Date.now();return outbox.enqueue(row,operation);
 };
 form.addEventListener('production-step-change',event=>{step=event.detail;lastActivity=Date.now();if(Date.now()-lastSync>10000)schedule(100);});
 form.addEventListener('production-autosave-committed',()=>{lastActivity=Date.now();schedule(3000);});
 form.addEventListener('input',()=>{lastActivity=Date.now();});
 form.addEventListener('production-network-time',event=>{if(event.detail.slow)slow=true;});
 form.querySelector('[data-production-refresh]')?.addEventListener('click',()=>{if(outbox.pending)outbox.schedule(0);else poll(true);});
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)schedule(100);});
 addEventListener('online',()=>{failures=0;schedule(100);});
 window.addEventListener('pagehide',()=>{closed=true;clearTimeout(timer);navigator.sendBeacon(form.dataset.liveUrl,new URLSearchParams({...scope,action:'close',csrfmiddlewaretoken:csrf}));});
 window.addEventListener('pageshow',()=>{closed=false;schedule(100);});
 schedule(100);
})();
