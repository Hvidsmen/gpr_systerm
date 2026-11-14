(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form)return;
 const catalogue=JSON.parse(document.getElementById('production-work-choices')?.textContent||'[]');
 const workKinds=new Map(JSON.parse(document.getElementById('production-work-meta')?.textContent||'[]').map(row=>[String(row.id),row.kind]));
 const compositeWorks=new Set(catalogue.filter(row=>row.kind==='COMPOSITE').map(row=>String(row.work)));
 const itemOwners=new Map(catalogue.filter(row=>row.item).map(row=>[String(row.item),String(row.work)]));
 // randomUUID is restricted to secure contexts; getRandomValues also works on HTTP.
 const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
 const sessionStorageKey='production-session:'+location.pathname+':'+form.elements.construction_object.value+':'+form.elements.date.value;
 const storedSession=sessionStorage.getItem(sessionStorageKey);
 const url=form.dataset.claimUrl,csrf=form.querySelector('[name=csrfmiddlewaretoken]').value,session=storedSession||`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
 sessionStorage.setItem(sessionStorageKey,session);
 const hidden=document.createElement('input');hidden.type='hidden';hidden.name='claim_session';hidden.value=session;form.append(hidden);
 const scope={actor_id:Number(form.dataset.userId),company_id:Number(form.dataset.companyId),construction_object:form.elements.construction_object.value,date:form.elements.date.value,session,reset_epoch:Number(form.dataset.resetEpoch||0)};
 let claims=new Map(),generation=0,busy=false,lastActivity=Date.now(),lastRenew=0,submitting=false;
 const rows=()=>[...form.querySelectorAll('[data-work-fact-row],[data-production-person]')],person=row=>row.matches('[data-production-person]'),field=(row,name)=>row.querySelector(`[name$="-${name}"]`),editing=row=>field(row,person(row)?'person_editing':'work_editing'),token=row=>field(row,person(row)?'person_claim_token':'work_claim_token');
 const keyOf=row=>{
  if(person(row))return field(row,'employee')?.value?`employee:${field(row,'employee').value}:${Number(field(row,'shift').value)}`:'';
  const work=field(row,'project_work')?.value,item=field(row,'work_item')?.value;
  if(work&&field(row,'operational_name')?.value.trim())return `operational:${row.dataset.factId||field(row,'operational_name').name}`;
  const composite=compositeWorks.has(String(work))||workKinds.get(String(work))==='COMPOSITE';
  if(!work||composite&&!item||item&&itemOwners.get(String(item))!==String(work))return '';
  return item?`item:${work}:${item}`:`work:${work}`;
 };
 const personRows=()=>rows().filter(person),deleted=row=>Boolean(field(row,'DELETE')?.checked);
 const existingWork=row=>{const key=keyOf(row);return key&&!person(row)&&rows().find(other=>other!==row&&!person(other)&&!deleted(other)&&keyOf(other)===key);};
 function focusExistingWork(row,conflict){
  window.productionAutoSave?.discardDraft(row);row.remove();window.productionAutoSave?.reindex();
  let group=conflict.closest('details');while(group){group.open=true;group=group.parentElement.closest('details');}
  conflict.querySelector('[data-error]').textContent='Эта работа уже добавлена за выбранный день. Измените объём в этой строке.';
  conflict.classList.add('production-new-row-focus');conflict.scrollIntoView({behavior:'smooth',block:'center'});
  setTimeout(()=>conflict.classList.remove('production-new-row-focus'),2200);field(conflict,'actual_quantity')?.focus({preventScroll:true});
 }
 const samePerson=row=>person(row)&&keyOf(row)&&personRows().filter(other=>!deleted(other)&&keyOf(other)===keyOf(row));
 function duplicate(row){
  if(!person(row)||deleted(row))return false;
  const matches=samePerson(row);if(!matches||matches.length<2)return false;
  matches.sort((a,b)=>Number(!field(a,'id')?.value)-Number(!field(b,'id')?.value)||Number(field(a,'employee').name.match(/-(\d+)-employee$/)?.[1]||0)-Number(field(b,'employee').name.match(/-(\d+)-employee$/)?.[1]||0));
  return matches[0]!==row;
 }
 const previousKeys=row=>JSON.parse(row.dataset.previousShiftKeys||'[]');
 const keysOf=row=>[...new Set([keyOf(row),...previousKeys(row)].filter(Boolean))];
 const duplicateText='Этот сотрудник уже выбран в этой смене. Измените существующую строку или уберите повтор.';
 function filterWorkItems(row){
  if(!row||person(row))return;
  const work=field(row,'project_work'),item=field(row,'work_item');if(!work||!item||item.tagName!=='SELECT')return;
  const owner=String(work.value||''),selected=String(item.value||'');
  for(const option of item.options){const allowed=!option.value||Boolean(owner)&&itemOwners.get(String(option.value))===owner;option.hidden=!allowed;option.disabled=!allowed;}
  if(selected&&itemOwners.get(selected)!==owner){item.value='';row.querySelector('[data-error]').textContent='Выберите работу в составе выбранного комплекса.';}
  item.dispatchEvent(new Event('work-options-updated'));
 }
 const originalOptionState=new WeakMap();
 function refreshPeople(){
  const records=personRows(),used=new Map();
  for(const row of records){if(deleted(row))continue;const key=keyOf(row);if(key){if(!used.has(key))used.set(key,[]);used.get(key).push(row);}}
  for(const row of records){const employee=field(row,'employee'),shift=Number(field(row,'shift').value);
   for(const option of employee.options){
    if(!originalOptionState.has(option))originalOptionState.set(option,option.disabled);
    const existing=option.value&&option.value!==employee.value?used.get(`employee:${option.value}:${shift}`)?.[0]:null;
    if(!option.dataset.baseEmployeeLabel)option.dataset.baseEmployeeLabel=option.textContent.replace(/ · уже добавлен в смену — открыть строку$/,'');
    option.textContent=option.dataset.baseEmployeeLabel+(existing?' · уже добавлен в смену — открыть строку':'');
    if(existing&&!existing.id)existing.id='production-person-'+field(existing,'employee').name.replace(/[^a-zA-Z0-9_-]/g,'-');
    option.dataset.existingPerson=existing?.id||'';
    // Keep a duplicate result clickable: selecting it focuses the existing
    // row below instead of silently showing an unexplained grey option.
    const blocked=originalOptionState.get(option);if(option.disabled!==blocked)option.disabled=blocked;
   }
   if(duplicate(row)){row.dataset.duplicatePerson='1';row.dataset.entryDirty='1';row.querySelector('[data-error]').textContent=duplicateText;}
   else if(row.dataset.duplicatePerson){delete row.dataset.duplicatePerson;if(row.querySelector('[data-error]').textContent===duplicateText)row.querySelector('[data-error]').textContent='';}
  }
 }
 // Reject a forced duplicate before target widgets and other form listeners see it.
 form.addEventListener('change',event=>{
  const workRow=event.target.closest('[data-work-fact-row]');
  if(workRow&&event.target===field(workRow,'project_work'))filterWorkItems(workRow);
  if(workRow&&!field(workRow,'id')?.value&&(event.target===field(workRow,'project_work')||event.target===field(workRow,'work_item'))){const conflict=existingWork(workRow);if(conflict){event.preventDefault();event.stopImmediatePropagation();focusExistingWork(workRow,conflict);return;}}
  const row=event.target.closest('[data-production-person]');if(!row)return;
  if(event.target!==field(row,'employee')&&event.target!==field(row,'shift'))return;
  const conflict=personRows().find(other=>other!==row&&!deleted(other)&&!deleted(row)&&keyOf(other)&&keyOf(other)===keyOf(row));
  if(conflict){field(row,'employee').value=row.dataset.acceptedEmployee||'';field(row,'shift').value=row.dataset.acceptedShift||'1';row.querySelector('[data-error]').textContent=duplicateText;conflict.scrollIntoView({behavior:'smooth',block:'center'});conflict.classList.add('production-person-focus');setTimeout(()=>conflict.classList.remove('production-person-focus'),2200);field(conflict,'employee')?.nextElementSibling?.querySelector('button')?.focus({preventScroll:true});}
  else if(event.target===field(row,'shift')&&field(row,'employee').value&&field(row,'shift').value!==row.dataset.acceptedShift&&!row.dataset.shiftApplying){
   event.stopImmediatePropagation();changeShift(row,field(row,'shift').value);return;
  }
  else{row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;if(row.querySelector('[data-error]').textContent===duplicateText)row.querySelector('[data-error]').textContent='';}
 },true);
 form.addEventListener('search-select-existing',event=>{const conflict=document.getElementById(event.detail?.row||'');if(!conflict)return;conflict.scrollIntoView({behavior:'smooth',block:'center'});conflict.classList.add('production-person-focus');setTimeout(()=>conflict.classList.remove('production-person-focus'),2200);field(conflict,'employee')?.nextElementSibling?.querySelector('button')?.focus({preventScroll:true});});
 form.addEventListener('input',event=>{if(event.target.closest('[data-production-person]')&&event.target.name?.endsWith('-shift'))event.stopImmediatePropagation();},true);
 const controls=row=>[...row.querySelectorAll(person(row)?'[name$="-work_mode"],[name$="-planning_position"],[name$="-activity"],[name$="-DELETE"]':'[name$="-operational_name"],[name$="-operational_unit"],[name$="-actual_quantity"],[name$="-comment"],[name$="-actuation_type"],[name$="-actuation_reason"],[name$="-actuation_reasons"],[name$="-DELETE"]')];
 async function request(action,key,extra={}){
  let lastError;
  for(let attempt=0;attempt<2;attempt++){
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
   try{const response=await fetch(url,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...scope,action,key,...extra})});const data=(response.headers.get('content-type')||'').includes('application/json')?await response.json():{};if(!response.ok){if(data.auth_changed)form.dataset.authChanged='1';if(data.claim)claims.set(data.claim.key,data.claim);const error=new Error(data.error||'Не удалось проверить резерв.');error.status=response.status;error.retryable=response.status>=500||response.status===429;throw error;}return data;}
   catch(error){
    if(error.name==='AbortError'){error=new Error('Сервер отвечает долго. Ввод остался в форме.');error.retryable=true;}
    else if(error instanceof TypeError){error=new Error('Не удалось связаться с сервером. Изменения остались в форме.');error.retryable=true;}
    lastError=error;if(!error.retryable||attempt>0||navigator.onLine===false)throw error;await new Promise(resolve=>setTimeout(resolve,600));
   }
   finally{clearTimeout(timeout);}
  }
  throw lastError;
 }

 function render(row){
  // Live refreshes can leave a stale dirty marker after the exact row values
  // were already acknowledged by autosave. Never show claim recovery for a
  // byte-for-byte saved row; a real subsequent edit changes the fingerprint.
  const autosaveState=row.querySelector('[data-autosave-row-state]')?.textContent.trim();
  if(row.dataset.entryDirty&&!row.dataset.autoPending&&(autosaveState==='Сохранено'||row.dataset.autoSavedStamp&&window.productionAutoSave?.fingerprint(row)===row.dataset.autoSavedStamp)){delete row.dataset.entryDirty;delete row.dataset.personDirty;}
  const repeated=duplicate(row),claim=claims.get(keyOf(row)),mine=!repeated&&claim?.state==='editing'&&claim.mine,bar=row.querySelector('[data-entry-claim]');if(!bar)return;
  const blocked=repeated||Boolean(claim&&!mine&&(claim.state==='editing'||!claim.own));
  controls(row).forEach(i=>{i.disabled=blocked;});row.querySelectorAll('[data-person-remove],[data-work-remove],[data-work-clear]').forEach(button=>{const restore=deleted(row)&&button.matches('[data-person-remove],[data-work-remove]');button.toggleAttribute('disabled',blocked&&!restore);});editing(row).value=mine?'1':'0';token(row).value=mine?session:'';
  if(deleted(row))form.productionRenderPendingDelete?.(row,true);
  // Employee identity stays fixed; shift changes reserve source and destination.
  for(const name of person(row)?['employee','shift']:['project_work','work_item']){const input=field(row,name);if(input)input.disabled=person(row)&&name==='shift' ? blocked||form.dataset.personShiftBusy==='1' : blocked||Boolean(keyOf(row)&&(mine||row.dataset.entryDirty||person(row)&&field(row,'id')?.value));}
  const take=bar.querySelector('[data-take]'),release=bar.querySelector('[data-release]');take.hidden=true;take.disabled=repeated||!keyOf(row)||Boolean(claim&& !mine&& (claim.state==='editing'||!claim.own));release.hidden=!row.dataset.entryDirty||mine;release.textContent=row.dataset.entryDirty?'Отменить изменения':'Освободить';
  bar.querySelector('[data-label]').textContent=repeated?'Повтор':mine?'Ваш резерв':claim?.state==='editing'?'Резерв: '+claim.owner+(claim.own?' · другая вкладка':''):claim?.state==='saved'?(claim.own?'Внёс: '+claim.owner+' · можно исправить':'Уже учтено: '+claim.owner+(claim.other_object?' · другой объект':' · только просмотр')):row.dataset.entryDirty?(row.dataset.claimRetry?'Нужно повторить сохранение':'Восстанавливаем резерв…'):keyOf(row)?'Запись свободна':field(row,'id')?.value?'Запись сохранена':person(row)?'Выберите сотрудника и смену':'Заполните объём работы';
  if(person(row))bar.querySelector('[data-label]').title=bar.querySelector('[data-label]').textContent;
 }
 async function changeShift(row,desired){
  const input=field(row,'shift'),before=row.dataset.acceptedShift||'1',error=row.querySelector('[data-error]');
  input.value=before;
  if(form.dataset.personShiftBusy==='1')return;
  form.dataset.personShiftBusy='1';rows().forEach(render);error.textContent='';
  let applied=false;
  try{
   // Finish any request already in flight with its original identity intact.
   if(window.productionAutoSave?.running&&!await window.productionAutoSave.whenIdle())throw new Error('Сначала исправьте ошибку сохранения. Смена не изменена.');
   const source=keyOf(row),target=`employee:${field(row,'employee').value}:${Number(desired)}`;
   generation++;
   if(!claims.get(source)?.mine){const result=await request('take',source);claims.set(source,result.claim);}
   if(!claims.get(target)?.mine){const result=await request('take',target);claims.set(target,result.claim);}
   row.dataset.previousShiftKeys=JSON.stringify([...new Set([...previousKeys(row),source])]);
   input.value=desired;row.dataset.acceptedShift=desired;row.dataset.entryKey=target;row.dataset.entryDirty='1';applied=true;
  }catch(failure){input.value=before;row.dataset.acceptedShift=before;error.textContent=failure.message;}
  finally{
   delete form.dataset.personShiftBusy;changed();
   if(applied){row.dataset.shiftApplying='1';input.dispatchEvent(new Event('change',{bubbles:true}));delete row.dataset.shiftApplying;}
  }
 }
 function capture(row){return [...row.querySelectorAll('input,select,textarea')].filter(i=>i.name&&!['person_claim_token','person_editing','work_claim_token','work_editing'].some(n=>i.name.endsWith('-'+n))).map(i=>({name:i.name,value:i.value,checked:i.checked,type:i.type}));}
 function reset(row){const baseline=JSON.parse(row.dataset.entryOriginal);row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());for(const entry of baseline){if(entry.name.endsWith('-work_targets')){const i=document.createElement('input');i.type='hidden';i.name=entry.name;i.value=entry.value;row.append(i);}else{const i=[...row.querySelectorAll('[name]')].find(i=>i.name===entry.name&&(entry.type!=='checkbox'||i.value===entry.value));if(i){i.value=entry.value;if(entry.type==='checkbox')i.checked=entry.checked;}}}}
 function wire(){for(const row of rows()){filterWorkItems(row);if(row.dataset.entryWired)continue;row.dataset.entryWired='1';row.dataset.entryOriginal=JSON.stringify(capture(row));row.dataset.entryKey=keyOf(row);if(person(row)){row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;}
   if(!row.querySelector('[data-entry-claim]')){const bar=document.createElement('div');bar.dataset.entryClaim='';bar.className='work-claim-bar';
   for(const [tag,attr,text] of [['span','label',''],['button','take','Резерв'],['button','release','Освободить'],['span','error','']]){const el=document.createElement(tag);el.dataset[attr]='';el.textContent=text;if(tag==='button'){el.type='button';el.className='btn btn-sm btn-outline-primary';}else el.className=attr==='error'?'small text-danger':'small';bar.append(el);}row.prepend(bar);}
   if(form.dataset.productionBound==='1'&&editing(row)?.value==='1'&&(person(row)?field(row,'employee')?.value:field(row,'actual_quantity')?.value))row.dataset.entryDirty='1';render(row);
  }}
 function changed(){refreshPeople();rows().forEach(render);form.dispatchEvent(new Event('production-claims-change'));}
 form.addEventListener('click',async event=>{const take=event.target.closest('[data-take]'),release=event.target.closest('[data-release]');if(!take&&!release)return;const row=event.target.closest('[data-work-fact-row],[data-production-person]'),key=keyOf(row),error=row.querySelector('[data-error]'),wasDuplicate=duplicate(row);if(wasDuplicate&&take)return;error.textContent='';busy=true;generation++;
  try{if(take){const data=await request('take',key);claims.set(key,data.claim);if(person(row)&&!field(row,'id').value)row.dataset.entryDirty='1';}else{await form.productionReleaseRow(row);reset(row);window.productionAutoSave?.discardDraft(row);if(wasDuplicate&&person(row)&&!field(row,'id').value){field(row,'employee').value='';delete row.dataset.duplicatePerson;}row.dataset.entryKey=keyOf(row);if(person(row)){row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;}delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;claims.delete(key);}render(row);changed();lastActivity=Date.now();}catch(failure){error.textContent=failure.message;render(row);}finally{busy=false;}
 });
 form.addEventListener('input',e=>{const row=e.target.closest('[data-work-fact-row],[data-production-person]');if(row&&controls(row).includes(e.target)&&!(e.target.name?.endsWith('-DELETE')&&!field(row,'id')?.value))row.dataset.entryDirty='1';lastActivity=Date.now();});
 form.addEventListener('change',e=>{wire();const row=e.target.closest('[data-work-fact-row],[data-production-person]');if(row){if(row.dataset.personDirty||controls(row).includes(e.target)&&!(e.target.name?.endsWith('-DELETE')&&!field(row,'id')?.value))row.dataset.entryDirty='1';const old=row.dataset.entryKey,key=keyOf(row);if(old!==key){if(claims.get(old)?.mine){generation++;request('release',old).catch(()=>{});claims.delete(old);}row.dataset.entryKey=key;}render(row);}refreshPeople();rows().forEach(render);lastActivity=Date.now();});
 async function poll(){if(document.hidden||busy)return;const version=generation;try{const response=await fetch(url+'?'+new URLSearchParams(scope),{credentials:'same-origin'});if(!response.ok)return;const data=await response.json();if(version!==generation)return;claims=new Map(data.claims.map(c=>[c.key,c]));if(data.report)form.dispatchEvent(new CustomEvent('production-report-update',{detail:data.report}));wire();rows().forEach(render);changed();}catch(error){/* Retry without clearing input. */}}
 form.productionClaimPeople=async selected=>{
  const acquired=[];
  try{for(let start=0;start<selected.length;start+=100){const keys=selected.slice(start,start+100).map(item=>`employee:${item.employee}:${item.shift}`),data=await request('take_many',null,{keys});for(const claim of data.claims){if(!claims.get(claim.key)?.mine)acquired.push(claim.key);claims.set(claim.key,claim);}}}
  catch(error){for(let i=0;i<acquired.length;i+=100)try{await request('release_many',null,{keys:acquired.slice(i,i+100)});for(const key of acquired.slice(i,i+100))claims.delete(key);}catch{}throw error;}
  changed();
 };
 form.productionForgetClaims=(selected,needsRetry=false)=>{for(const row of selected){if(needsRetry)row.dataset.claimRetry='1';for(const key of keysOf(row))if(claims.get(key)?.mine)claims.delete(key);}changed();};
 form.addEventListener('production-people-bulk-staged',()=>{wire();changed();});

 form.productionValidatePeople=()=>{refreshPeople();const repeated=personRows().find(duplicate);if(repeated){repeated.querySelector('[data-error]').textContent=duplicateText;throw new Error(duplicateText);}};
 const acquiring=new Map();
 form.productionClaimRow=async row=>{
  if(duplicate(row))throw new Error(duplicateText);const key=keyOf(row);if(!key){if(!person(row)&&field(row,'project_work')?.value&&(compositeWorks.has(String(field(row,'project_work').value))||workKinds.get(String(field(row,'project_work').value))==='COMPOSITE'))throw new Error('Выберите работу в составе выбранного комплекса.');return;}
  if(key.startsWith('operational:')){delete row.dataset.claimRetry;claims.set(key,{key,state:'editing',mine:true,own:true,owner:'Вы'});row.querySelector('[data-error]').textContent='';render(row);row.dataset.entryDirty='1';changed();return;}
  if(!claims.get(key)?.mine){
   if(!acquiring.has(key)){generation++;acquiring.set(key,request('take',key).then(data=>{claims.set(key,data.claim);}).finally(()=>acquiring.delete(key)));}
   try{await acquiring.get(key);}catch(error){row.dataset.claimRetry='1';render(row);throw error;}
  }
  delete row.dataset.claimRetry;row.querySelector('[data-error]').textContent='';
  render(row);row.dataset.entryDirty='1';changed();lastActivity=Date.now();
 };
 form.productionSetClaims=values=>{for(const row of personRows())if(!row.dataset.entryDirty&&!row.dataset.personDirty&&!row.dataset.autoPending){row.dataset.acceptedShift=field(row,'shift').value;row.dataset.entryKey=keyOf(row);}claims=new Map(values.map(c=>[c.key,c]));wire();changed();};
 form.productionEmployeeAvailability=(employee,shift)=>{
  const claim=claims.get(`employee:${employee}:${Number(shift)}`);
  if(!claim||claim.state==='editing'&&claim.mine)return {available:true,label:''};
  return {available:false,label:claim.state==='editing'?'Сейчас заполняет: '+claim.owner:claim.other_object?'Уже учтён на другом объекте в этой смене':'Уже учтён в этой смене'};
 };
 form.productionKeyOf=keyOf;
 form.productionKeysOf=keysOf;
 form.productionSession={scope,csrf};
 form.productionReleaseRow=async row=>{for(const key of keysOf(row)){if(claims.get(key)?.mine){if(!key.startsWith('operational:'))await request('release',key);claims.delete(key);}}delete row.dataset.previousShiftKeys;render(row);changed();};
 const reserve=event=>{
  if(form.productionApplying||form.dataset.resetRequired==='1')return;
  const row=event.target.closest('[data-work-fact-row],[data-production-person]');if(!row||!keyOf(row)||!event.target.name)return;
  if(controls(row).includes(event.target)||['employee','shift'].some(name=>event.target===field(row,name))){
   row.dataset.entryDirty='1';form.productionClaimRow(row).catch(error=>{row.querySelector('[data-error]').textContent=error.message;render(row);});
  }
 };
 form.addEventListener('input',reserve);form.addEventListener('change',reserve);
 form.addEventListener('production-autosave-committed',()=>{for(const row of personRows()){if(row.dataset.entryDirty||row.dataset.personDirty||row.dataset.autoPending)continue;const stale=previousKeys(row).filter(key=>key!==keyOf(row));delete row.dataset.previousShiftKeys;for(const key of stale)if(claims.get(key)?.mine){claims.delete(key);request('release',key).catch(()=>{});}}wire();refreshPeople();rows().forEach(render);changed();});
 form.addEventListener('submit',event=>{if(event.defaultPrevented)return;refreshPeople();const repeated=personRows().find(duplicate);if(repeated){event.preventDefault();form.querySelector('[data-step="2"]')?.click();repeated.querySelector('[data-error]').textContent=duplicateText;repeated.scrollIntoView({block:'center'});return;}const lost=rows().find(row=>!(field(row,'DELETE')?.checked&&!field(row,'id')?.value)&&(row.dataset.entryDirty||row.dataset.personDirty)&&!claims.get(keyOf(row))?.mine);if(lost){event.preventDefault();const panel=lost.closest('[data-entry-step]');form.querySelector(`[data-step="${panel.dataset.entryStep}"]`)?.click();let group=lost.closest('details');while(group){group.open=true;group=group.parentElement.closest('details');}lost.querySelector('[data-error]').textContent='Возьмите запись снова или отмените изменения. Введённые значения сохранены в форме.';lost.scrollIntoView({block:'center'});return;}
  // Disabled identity controls still have to be posted for owned rows.
  rows().forEach(row=>{if(claims.get(keyOf(row))?.mine){for(const name of person(row)?['employee','shift']:['project_work','work_item']){const input=field(row,name);if(input)input.disabled=false;}}});submitting=true;
 });
 window.addEventListener('beforeunload',e=>{if(!submitting&&rows().some(r=>r.dataset.entryDirty||r.dataset.personDirty)){e.preventDefault();e.returnValue='';}});
 new MutationObserver(wire).observe(form,{childList:true,subtree:true});wire();refreshPeople();rows().forEach(render);
})();
