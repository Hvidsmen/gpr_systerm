(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form||form.dataset.readonly==='1')return;
 const state=form.querySelector('[data-autosave-state]'),retry=form.querySelector('[data-autosave-retry]');
 const recovery=document.createElement('div'),recoveryText=document.createElement('span'),reload=document.createElement('button');recovery.className='production-recovery-advice alert alert-warning';recovery.hidden=true;recovery.setAttribute('role','alert');reload.type='button';reload.className='btn btn-sm btn-outline-primary';reload.textContent='Перезагрузить страницу';reload.hidden=true;reload.addEventListener('click',()=>location.reload());recovery.append(recoveryText,reload);form.querySelector('[data-links-sync]')?.after(recovery);
 function recoveryState(text,allowReload=false){recoveryText.textContent=text;reload.hidden=!allowReload;recovery.hidden=false;}
 form.productionRecovery={offline:()=>recoveryState('Нет соединения с сервером. Изменения сохранены в браузере. Не закрывайте страницу.'),online:()=>recoveryState('Соединение восстановлено. Нажмите «Сохранить», чтобы отправить оставшиеся изменения.'),reload:text=>recoveryState(text||'Не удалось синхронизировать форму с сервером. Черновик сохранён в браузере. Перезагрузите страницу — он восстановится автоматически.',true),clear:()=>{recovery.hidden=true;reload.hidden=true;}};
 const progress=document.createElement('div'),progressLabel=document.createElement('span'),progressTrack=document.createElement('progress');let progressTimer=null;
 progress.className='production-operation-progress';progress.hidden=true;progressTrack.max=100;progress.append(progressLabel,progressTrack);form.querySelector('.production-save-footer')?.prepend(progress);
 function progressState(kind,label,done=0,total=0){clearTimeout(progressTimer);progress.hidden=false;progress.dataset.state=kind;progressLabel.textContent=label;if(total)progressTrack.value=Math.round(done/total*100);else progressTrack.removeAttribute('value');progressTrack.classList.toggle('is-indeterminate',!total);if(kind==='done')progressTimer=setTimeout(()=>{progress.hidden=true;},2500);}
 form.productionStatus={start:(label,total=0)=>progressState('loading',label,0,total),update:(label,done,total)=>progressState('loading',label,done,total),done:label=>progressState('done',label,1,1),error:label=>progressState('error',label,1,1)};
 document.querySelector('[data-fact-selector]')?.addEventListener('submit',()=>progressState('loading','Открываем выбранный объект и день…'));
 const rows=()=>[...form.querySelectorAll('[data-production-person],[data-work-fact-row]')];
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),isPerson=row=>row.hasAttribute('data-production-person');
 const suffix=input=>input.name.replace(/^(works|labor)-\d+-/,'');
 const capture=row=>[...row.querySelectorAll('input,select,textarea')].filter(input=>input.name).map(input=>({name:input.name,value:input.value,checked:input.checked,type:input.type}));
 const fingerprint=row=>JSON.stringify(capture(row).filter(input=>!['id','person_claim_token','person_editing','work_claim_token','work_editing',...(isPerson(row)?['work_mode','work_targets','general','work','work_item']:[])].includes(input.name.replace(/^(works|labor)-\d+-/,''))).map(input=>[input.name.replace(/^(works|labor)-\d+-/,''),input.value,input.checked]));
 const draftPrefix=['yds-production-draft-v1',form.dataset.companyId,form.dataset.userId,form.elements.construction_object.value,form.elements.date.value,form.dataset.resetEpoch||'0'].join(':')+':';
 const draftIdentity=row=>{const id=field(row,'id')?.value;if(id)return (isPerson(row)?'labor:id:':'works:id:')+id;if(!row.dataset.browserDraftId)row.dataset.browserDraftId=crypto.randomUUID();return (isPerson(row)?'labor:new:':'works:new:')+row.dataset.browserDraftId;};
 function draftRecord(row){const relations=new Set(['work_mode','work_targets','general','work','work_item']),internal=new Set(['id','person_claim_token','person_editing','work_claim_token','work_editing']),values=capture(row).map(input=>({...input,name:input.name.replace(/^(works|labor)-\d+-/,'')})).filter(input=>!internal.has(input.name)&&(!isPerson(row)||!relations.has(input.name))),options={};row.querySelectorAll('select[name]').forEach(input=>{const selected=input.selectedOptions?.[0];if(selected)options[input.name.replace(/^(works|labor)-\d+-/,'')]={value:selected.value,label:selected.textContent};});return {kind:isPerson(row)?'labor':'works',identity:draftIdentity(row),values,options,updated:Date.now()};}
 function storeDraft(row){try{const record=draftRecord(row),key=draftPrefix+record.identity;if(row.dataset.draftStorageKey&&row.dataset.draftStorageKey!==key)localStorage.removeItem(row.dataset.draftStorageKey);localStorage.setItem(key,JSON.stringify(record));row.dataset.draftStorageKey=key;}catch{message('Не удалось сохранить локальный черновик. Не закрывайте страницу до сохранения на сервере.',true);}}
 function removeDraft(row){const key=row.dataset.draftStorageKey||draftPrefix+draftIdentity(row);try{localStorage.removeItem(key);}catch{}delete row.dataset.draftStorageKey;}
 function applyDraft(row,record){const prefixName=field(row,'id').name.replace(/-id$/,'');for(const saved of record.values){const candidates=[...row.querySelectorAll(`[name="${CSS.escape(prefixName+'-'+saved.name)}"]`)];const input=['checkbox','radio'].includes(saved.type)?candidates.find(item=>item.value===saved.value):candidates[0];if(!input)continue;const option=record.options?.[saved.name];if(input.tagName==='SELECT'&&saved.value&&!Array.from(input.options).some(item=>item.value===saved.value))input.add(new Option(option?.label||saved.value,saved.value));if(input.type==='checkbox'||input.type==='radio')input.checked=Boolean(saved.checked);else input.value=saved.value;}row.dataset.browserDraftId=record.identity.split(':').at(-1);row.dataset.draftStorageKey=draftPrefix+record.identity;row.dataset.entryDirty='1';row.dataset.autoPending='1';rowMessage(row,'Восстановлено из браузера');}
 function restoreDrafts(){const records=[];let restored=0;try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith(draftPrefix))continue;try{const record=JSON.parse(localStorage.getItem(key));if(record?.identity&&['works','labor'].includes(record.kind))records.push(record);}catch{}}}catch{}form.productionApplying=true;try{for(const record of records.sort((a,b)=>a.updated-b.updated)){let row;if(record.identity.includes(':id:')){const id=record.identity.split(':').at(-1);row=rows().find(item=>(isPerson(item)?'labor':'works')===record.kind&&field(item,'id')?.value===id);if(!row){try{localStorage.removeItem(draftPrefix+record.identity);}catch{}continue;}}else{const total=form.elements.namedItem(record.kind+'-TOTAL_FORMS'),host=form.querySelector(`[data-rows="${record.kind}"]`);if(!total||!host)continue;const index=Number(total.value);host.insertAdjacentHTML('beforeend',document.getElementById('empty-'+record.kind).innerHTML.replaceAll('__prefix__',index));row=host.lastElementChild;total.value=index+1;}if(row){applyDraft(row,record);row.dispatchEvent(new Event('change',{bubbles:true}));restored++;}}}finally{form.productionApplying=false;}return restored;}
 let timer=null,pending=false,running=null,failed=false,retryAttempt=0,lastMessage='',lastError=false;
 const dirty=()=>rows().filter(row=>row.dataset.entryDirty||row.dataset.personDirty||row.dataset.autoPending);
 function message(text,error=false){lastMessage=text;lastError=error;if(!error&&text==='Все изменения сохранены'&&Number(form.dataset.linksRejected||0)>0)text='Данные сохранены; часть связей требует проверки';if(!error&&text==='Все изменения сохранены'&&Number(form.dataset.linksPending||0)>0)text='Данные сохранены; связи ожидают отправки';state.textContent=text;state.classList.toggle('text-danger',error);state.classList.toggle('fact-state-saving',!error&&text==='Сохранение…');state.classList.toggle('fact-state-saved',!error&&text==='Все изменения сохранены');state.classList.toggle('fact-state-pending',!error&&text.includes('изменения')&&text!=='Все изменения сохранены');retry.hidden=!error;}
 function rowMessage(row,text,error=false){const target=row.querySelector('[data-autosave-row-state]');if(target){target.textContent=text;target.title=text;target.classList.toggle('text-danger',error);}row.classList.toggle('production-person-auto-failed',error);}
 function keepNewRowsVisible(){const url=new URL(location.href);let changed=false;for(const row of rows().filter(item=>item.dataset.newPinned)){const id=field(row,'id')?.value;if(!id)continue;const kind=row.dataset.newPinned,note=row.querySelector('[data-new-row-note]');if(note)note.textContent=(kind==='person'?'Новый сотрудник':'Новая работа')+' · сохранено · закреплено до перехода';url.searchParams.set(kind==='person'?'focus_person':'focus_work',id);changed=true;}if(changed)history.replaceState(null,'',url);}
 function renderPendingDelete(row,deleted=Boolean(field(row,'DELETE')?.checked)){
  row.hidden=false;row.classList.toggle('production-row-pending-delete',deleted);
  const remove=row.querySelector('[data-person-remove],[data-work-remove]'),restore=row.querySelector('[data-person-restore],[data-work-restore]');if(!remove||!restore)return;
  remove.hidden=deleted||(!isPerson(row)&&row.dataset.planned==='1');restore.hidden=!deleted;
 }
 form.productionRenderPendingDelete=renderPendingDelete;
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
  if(row){delete row.dataset.autoRejected;row.dataset.autoPending='1';rowMessage(row,field(row,'DELETE')?.checked?'Будет удалено':'Не сохранено');storeDraft(row);}
  pending=true;failed=false;retryAttempt=0;message('Есть несохранённые изменения');clearTimeout(timer);
 }
 function uuid(){
  if(crypto.randomUUID)return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const value=[...bytes].map(item=>item.toString(16).padStart(2,'0')).join('');return `${value.slice(0,8)}-${value.slice(8,12)}-${value.slice(12,16)}-${value.slice(16,20)}-${value.slice(20)}`;
 }
 function compactValues(row){
  const grouped=new Map();
  for(const input of row.querySelectorAll('input[name],select[name],textarea[name]')){
   const name=suffix(input);if(['id','person_editing','work_editing'].includes(name))continue;
   if((input.type==='checkbox'||input.type==='radio')&&!input.checked){if(!grouped.has(name))grouped.set(name,[]);continue;}
   const selected=input.tagName==='SELECT'&&input.multiple?[...input.selectedOptions].map(option=>option.value):[input.value];
   grouped.set(name,[...(grouped.get(name)||[]),...selected]);
  }
  // An unchecked checkbox group is an empty list, not a single empty-string
  // choice.  Sending "" made an untouched actuation-reasons field fail
  // validation when the user edited only the work quantity.
  const values={};for(const [name,items] of grouped)values[name]=items.length>1?items:items.length===1?items[0]:[];return values;
 }
 async function save(selected){
  const changed=selected||dirty();
  form.productionStatus.start(`Сохраняем текущую вкладку · записей: ${changed.length}`,changed.length);
  if(changed.some(row=>isPerson(row)&&!field(row,'DELETE')?.checked))form.productionValidatePeople();
  for(const row of changed){
   if(!isPerson(row)&&!field(row,'DELETE')?.checked&&field(row,'actual_quantity')?.value===''){
    const error=new Error('Введите фактический объём работы или 0. Пустое поле не сохраняется.');
    row.querySelector('[data-error]').textContent=error.message;throw error;
   }
  }
  for(const row of changed){
   if(isPerson(row)&&field(row,'employee').value){try{await form.productionClaimRow(row);}catch(error){if(field(row,'id').value||error.status!==409)throw error;row.dataset.autoRejected=error.message;field(row,'employee').value='';delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;row.dispatchEvent(new Event('change',{bubbles:true}));row.querySelector('[data-error]').textContent=error.message;rowMessage(row,error.message,true);}}
   else if(!isPerson(row)&&field(row,'project_work')?.value)await form.productionClaimRow(row);
  }
  const sent=new Map(changed.map(row=>[prefix(row),{row,stamp:fingerprint(row),capture:capture(row)}]));
  const retained=changed.map(row=>row.dataset.batchId).filter(Boolean),batchId=retained.length&&retained.every(value=>value===retained[0])?retained[0]:uuid();
  changed.forEach(row=>row.dataset.batchId=batchId);
  const body=JSON.stringify({batch_id:batchId,actor_id:Number(form.dataset.userId),company_id:Number(form.dataset.companyId),
   construction_object:form.elements.construction_object.value,date:form.elements.date.value,session:form.productionSession?.scope.session||'',
   rows:changed.map(row=>({kind:isPerson(row)?'labor':'works',prefix:prefix(row),id:field(row,'id')?.value||null,
    version:row.dataset.entryVersion||null,values:compactValues(row)}))});
  changed.forEach(row=>rowMessage(row,'Сохранение…'));message('Сохранение…');
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);let response;
  try{response=await fetch(form.dataset.batchSaveUrl,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body});}
  catch(error){form.productionForgetClaims?.(changed);if(error.name==='AbortError'){error=new Error('Сервер отвечает долго. Ввод остался в форме; повторим сохранение.');error.retryable=true;}throw error;}
  finally{clearTimeout(timeout);}
  if(!(response.headers.get('content-type')||'').includes('application/json')){const error=new Error('Не удалось сохранить изменения. Проверьте доступ к порталу и повторите сохранение.');error.retryable=response.status>=500||response.status===429;throw error;}
  const result=await response.json();
  if(!response.ok){
   for(const issue of result.errors||[]){const item=sent.get(issue.prefix);if(!item)continue;const text=issue.message||Object.values(issue.fields||{}).flat().join(' ');const target=item.row.querySelector('[data-error]');if(target)target.textContent=text;rowMessage(item.row,text,true);}
   const error=new Error(result.error||'Изменения не сохранены.');error.status=response.status;error.retryable=response.status>=500||response.status===429||Boolean(result.error?.includes('Резерв')&&result.error?.includes('истёк'));if(error.retryable)form.productionForgetClaims?.(changed);throw error;
  }
  // A successful autosave resolves the server-bound form state. Live updates
  // may rebuild rows afterwards; they must not be marked dirty again merely
  // because the original page was rendered from a POST.
  form.dataset.productionBound='0';
  for(const name of ['revision','labor_revision'])if(result[name]!==undefined)form.elements.namedItem(name).value=result[name];
  for(const entry of result.rows){
   const item=sent.get(entry.prefix);if(!item)continue;
   const row=item.row;
   if(entry.deleted){if(isPerson(row))form.productionLinks?.cancelPerson(Number(field(row,'id').value));else form.productionLinks?.cancelWork(Number(field(row,'id').value));removeDraft(row);try{await form.productionReleaseRow(row);}catch{}row.remove();continue;}
   if(!isPerson(row)&&Number(field(row,'actual_quantity').value.replace(',','.'))===0)form.productionLinks?.cancelWork(Number(entry.id));
   field(row,'id').value=entry.id;
   row.dataset.entryVersion=entry.version||'';delete row.dataset.batchId;
   if(!isPerson(row))row.dataset.factId=String(entry.id);
   if(isPerson(row)){const employee=field(row,'employee');if(employee.selectedOptions.length===1&&employee.options.length>1)employee.replaceChildren(employee.selectedOptions[0].cloneNode(true));}
   row.dataset.entryOriginal=JSON.stringify(item.capture.map(input=>input.name.endsWith('-id')?{...input,value:String(entry.id)}:input));
  }
  for(const item of sent.values()){
   const row=item.row;if(!row.isConnected)continue;
   if(fingerprint(row)===item.stamp){removeDraft(row);delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;row.dataset.autoSavedStamp=fingerprint(row);rowMessage(row,row.dataset.autoRejected|| (field(row,'id').value?'Сохранено':''),Boolean(row.dataset.autoRejected));if(!row.dataset.autoRejected)row.querySelector('[data-error]').textContent='';}
   else{row.dataset.autoPending='1';pending=true;storeDraft(row);rowMessage(row,'Есть изменения');}
  }
  form.productionStatus.update(`Сохранено записей: ${changed.length} из ${changed.length}`,changed.length,changed.length);
  reindex();keepNewRowsVisible();if(result.report){if(form.productionApplyReport)form.productionApplyReport(result.report);else form.dispatchEvent(new CustomEvent('production-report-update',{detail:result.report}));}form.dispatchEvent(new Event('production-autosave-committed'));
  message(pending?'Есть изменения…':'Все изменения сохранены');
 }
 async function flush(){
  if((form.dataset.peopleBulkBusy==='1'||form.dataset.personShiftBusy==='1')){clearTimeout(timer);timer=setTimeout(()=>flush(),700);return false;}
  if(form.dataset.resetRequired==='1'){clearTimeout(timer);message('Производственная часть сброшена администратором. Обновите форму.',true);form.productionRecovery.reload('Производственный день был сброшен. Черновик сохранён отдельно. Перезагрузите страницу и проверьте данные перед повторным сохранением.');return false;}
  clearTimeout(timer);if(running){pending=true;const current=running;await current;return new Promise(resolve=>setTimeout(()=>resolve(flush()),0));}
  running=(async()=>{
   while(pending||dirty().length){
    pending=false;const changed=dirty(),deletions=changed.filter(row=>field(row,'DELETE')?.checked),people=changed.filter(isPerson);
    const selected=(deletions.length?deletions:people.length?people:changed).slice(0,10);
    try{await save(selected);failed=false;retryAttempt=0;}catch(error){failed=true;retryAttempt++;selected.forEach(row=>{if(field(row,'DELETE')?.checked)row.hidden=false;rowMessage(row,row.querySelector('[data-error]')?.textContent||error.message,true);});message(error.message,true);form.productionStatus.error(error.message);if(error.status===409||form.dataset.authChanged==='1'||retryAttempt>=2)form.productionRecovery.reload();else if(navigator.onLine===false)form.productionRecovery.offline();break;}}
   if(!failed&&form.productionCrewDraft?.pending){
    form.productionStatus.start(`Сохраняем состав бригад · изменений: ${form.productionCrewDraft.pending}`,form.productionCrewDraft.pending);
    try{await form.productionCrewDraft.flush();}catch(error){failed=true;message(error.message,true);form.productionStatus.error(error.message);}
   }
   if(!failed&&form.productionLinks?.pending){
    form.productionStatus.start(`Сохраняем связи · изменений: ${form.productionLinks.pending}`,form.productionLinks.pending);
    if(!await form.productionLinks.flush()){failed=true;message('Связи не сохранены. Проверьте статус над вкладками и повторите сохранение.',true);form.productionStatus.error('Связи не сохранены');}
   }
   if(!failed){message('Все изменения сохранены');form.productionStatus.done('Изменения и связи сохранены');form.productionRecovery.clear();}
   return !failed;
  })();
  try{return await running;}finally{running=null;if(pending&&dirty().length)message('Есть несохранённые изменения');}
 }
 async function flushPeople(){
  clearTimeout(timer);if(running)await running;
  const selected=dirty().filter(isPerson).slice(0,10);if(!selected.length){if(dirty().length)timer=setTimeout(()=>flush(),700);return true;}
  running=(async()=>{try{await save(selected);return true;}catch(error){selected.forEach(row=>rowMessage(row,error.message,true));message(error.message,true);return false;}})();
  try{return await running;}finally{running=null;if(dirty().length){message('Сотрудники обработаны. Есть несохранённые изменения. Нажмите «Сохранить».');pending=true;clearTimeout(timer);}}
 }
 form.addEventListener('production-links-state',event=>{if(event.detail.pending)message(`Есть несохранённые связи: ${event.detail.pending}. Нажмите «Сохранить».`);else if(lastMessage)message(lastMessage,lastError);});
 window.productionAutoSave={whenIdle:()=>running||Promise.resolve(true),flush,flushPeople,reindex,fingerprint,capture,get running(){return Boolean(running);},get pending(){return pending||Boolean(running)||dirty().length>0||Boolean(form.productionCrewDraft?.pending)||Boolean(form.productionLinks?.pending);}};
 const relevant=event=>{
  if(form.productionApplying||(form.dataset.peopleBulkBusy==='1'||form.dataset.personShiftBusy==='1')||event.target.closest('[data-actuation-ui]'))return;
  const row=event.target.closest('[data-production-person],[data-work-fact-row]');
  if(row){renderPendingDelete(row);if(row.dataset.autoSavedStamp===fingerprint(row)&&!row.dataset.autoPending){delete row.dataset.entryDirty;delete row.dataset.personDirty;return;}if(isPerson(row)&&!field(row,'employee').value&&!field(row,'id').value)return;if(!isPerson(row)&&!field(row,'project_work')?.value)return;schedule(row);}
 };
 form.addEventListener('production-claims-change',()=>{if(!form.productionApplying&&form.dataset.peopleBulkBusy!=='1'&&form.dataset.personShiftBusy!=='1'&&!running&&!pending&&dirty().length)schedule();});
 // Capture edits before row widgets, filters or live-sync handlers can react.
 // The saved indicator must change synchronously with the user's input.
 form.addEventListener('input',relevant,true);form.addEventListener('change',relevant,true);
 form.addEventListener('production-autosave-all',()=>{for(const row of rows())if(isPerson(row)&&field(row,'employee').value&&!field(row,'id').value&&field(row,'person_editing').value==='1'){row.dataset.entryDirty='1';schedule(row);}});
 const restoredDrafts=restoreDrafts();rows().forEach(row=>{if(!row.dataset.autoPending)row.dataset.autoSavedStamp=fingerprint(row);if(field(row,'id').value&&!row.dataset.autoPending)rowMessage(row,'Сохранено');renderPendingDelete(row);if(field(row,'DELETE')?.checked)rowMessage(row,'Будет удалено');});
 // Claim wiring runs before this module. A bound form may therefore already
 // contain dirty rows whose earlier claims-change event could not be heard.
 // Explicitly resume them instead of leaving "Restoring reservation" forever.
 if(dirty().length){pending=true;message(restoredDrafts?`Восстановлен локальный черновик: ${restoredDrafts} записей. Нажмите «Сохранить».`:'Есть несохранённые изменения');}
 addEventListener('offline',()=>form.productionRecovery.offline());
 addEventListener('online',()=>{if(dirty().length||form.productionLinks?.pending){pending=true;message('Соединение восстановлено · нажмите «Сохранить»');form.productionRecovery.online();}else form.productionRecovery.clear();});
 retry.addEventListener('click',()=>{pending=true;flush();});
 form.querySelector('[data-production-save]')?.addEventListener('click',()=>flush());
 document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&(event.code==='KeyS'||event.key.toLocaleLowerCase()==='s')){event.preventDefault();event.stopPropagation();if(form.dataset.lockState==='mine')flush();}},true);
 form.addEventListener('click',async event=>{
  const clear=event.target.closest('[data-work-clear]');
  if(clear){
   event.preventDefault();
   const row=clear.closest('[data-work-fact-row]');
   if(row.dataset.planned!=='1'){row.querySelector('[data-work-remove]')?.click();return;}
   clear.disabled=true;
   try{
    await form.productionClaimRow(row);
    const quantity=field(row,'actual_quantity');quantity.value='0';
    for(const name of ['comment','actuation_type','actuation_reason']){const input=field(row,name);if(input)input.value='';}
    row.querySelectorAll('[name$="-actuation_reasons"]').forEach(input=>{input.checked=false;});
    row.dataset.entryDirty='1';quantity.dispatchEvent(new Event('input',{bubbles:true}));schedule(row);
   }
   catch(error){rowMessage(row,error.message,true);message(error.message,true);}
   finally{clear.disabled=false;}
   return;
  }
  const button=event.target.closest('[data-person-remove],[data-work-remove],[data-person-restore],[data-work-restore]');if(!button)return;
  event.preventDefault();
  const row=button.closest('[data-production-person],[data-work-fact-row]');button.disabled=true;
  try{
   if(field(row,'DELETE')?.checked){
    field(row,'DELETE').checked=false;renderPendingDelete(row,false);delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;removeDraft(row);
    row.dispatchEvent(new Event('change',{bubbles:true}));
    if(row.dataset.autoSavedStamp!==fingerprint(row))schedule(row);else rowMessage(row,field(row,'id').value?'Сохранено':'');
    return;
   }
   if(!field(row,'id').value&&!running){removeDraft(row);try{await form.productionReleaseRow(row);}catch{}row.remove();reindex();form.dispatchEvent(new Event('change',{bubbles:true}));return;}
   await form.productionClaimRow(row);field(row,'DELETE').checked=true;row.dataset.entryDirty='1';renderPendingDelete(row,true);rowMessage(row,'Будет удалено');row.dispatchEvent(new Event('change',{bubbles:true}));schedule(row);
  }catch(error){row.hidden=false;rowMessage(row,error.message,true);message(error.message,true);}finally{if(row.isConnected)row.classList.remove('production-row-auto-deleting');button.disabled=false;}
 });
 form.addEventListener('submit',event=>{event.preventDefault();flush();},true);
 window.addEventListener('beforeunload',event=>{if(running||pending||dirty().length){event.preventDefault();event.returnValue='';}});
})();
