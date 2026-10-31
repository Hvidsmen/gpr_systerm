(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form)return;
 // randomUUID is restricted to secure contexts; getRandomValues also works on HTTP.
 const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
 const sessionStorageKey='production-session:'+location.pathname+':'+form.elements.construction_object.value+':'+form.elements.date.value;
 const storedSession=sessionStorage.getItem(sessionStorageKey);
 const url=form.dataset.claimUrl,csrf=form.querySelector('[name=csrfmiddlewaretoken]').value,session=storedSession||`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
 sessionStorage.setItem(sessionStorageKey,session);
 const hidden=document.createElement('input');hidden.type='hidden';hidden.name='claim_session';hidden.value=session;form.append(hidden);
 const scope={construction_object:form.elements.construction_object.value,date:form.elements.date.value,session,reset_epoch:Number(form.dataset.resetEpoch||0)};
 let claims=new Map(),generation=0,busy=false,lastActivity=Date.now(),lastRenew=0,submitting=false;
 const rows=()=>[...form.querySelectorAll('[data-work-fact-row],[data-production-person]')],person=row=>row.matches('[data-production-person]'),field=(row,name)=>row.querySelector(`[name$="-${name}"]`),editing=row=>field(row,person(row)?'person_editing':'work_editing'),token=row=>field(row,person(row)?'person_claim_token':'work_claim_token');
 const keyOf=row=>person(row)?(field(row,'employee')?.value?`employee:${field(row,'employee').value}:${Number(field(row,'shift').value)}`:''):(field(row,'project_work')?.value?(field(row,'work_item')?.value?`item:${field(row,'project_work').value}:${field(row,'work_item').value}`:`work:${field(row,'project_work').value}`):'');
 const personRows=()=>rows().filter(person),deleted=row=>Boolean(field(row,'DELETE')?.checked);
 const samePerson=row=>person(row)&&keyOf(row)&&personRows().filter(other=>!deleted(other)&&keyOf(other)===keyOf(row));
 function duplicate(row){
  if(!person(row)||deleted(row))return false;
  const matches=samePerson(row);if(!matches||matches.length<2)return false;
  matches.sort((a,b)=>Number(!field(a,'id')?.value)-Number(!field(b,'id')?.value)||Number(field(a,'employee').name.match(/-(\d+)-employee$/)?.[1]||0)-Number(field(b,'employee').name.match(/-(\d+)-employee$/)?.[1]||0));
  return matches[0]!==row;
 }
 const duplicateText='Этот сотрудник уже выбран в этой смене. Измените существующую строку или уберите повтор.';
 const originalOptionState=new WeakMap();
 function refreshPeople(){
  const records=personRows();
  for(const row of records){const employee=field(row,'employee'),shift=Number(field(row,'shift').value),used=new Set(records.filter(other=>other!==row&&!deleted(other)&&Number(field(other,'shift').value)===shift).map(other=>field(other,'employee').value));
   for(const option of employee.options){if(!originalOptionState.has(option))originalOptionState.set(option,option.disabled);const blocked=originalOptionState.get(option)||Boolean(option.value&&option.value!==employee.value&&used.has(option.value));if(option.disabled!==blocked)option.disabled=blocked;}
   if(duplicate(row)){row.dataset.duplicatePerson='1';row.querySelector('[data-error]').textContent=duplicateText;}
   else if(row.dataset.duplicatePerson){delete row.dataset.duplicatePerson;if(row.querySelector('[data-error]').textContent===duplicateText)row.querySelector('[data-error]').textContent='';}
  }
 }
 // Reject a forced duplicate before target widgets and other form listeners see it.
 form.addEventListener('change',event=>{
  const row=event.target.closest('[data-production-person]');if(!row)return;
  if(event.target!==field(row,'employee')&&event.target!==field(row,'shift'))return;
  const conflict=personRows().some(other=>other!==row&&!deleted(other)&&!deleted(row)&&keyOf(other)&&keyOf(other)===keyOf(row));
  if(conflict){field(row,'employee').value=row.dataset.acceptedEmployee||'';field(row,'shift').value=row.dataset.acceptedShift||'1';row.querySelector('[data-error]').textContent=duplicateText;}
  else{row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;if(row.querySelector('[data-error]').textContent===duplicateText)row.querySelector('[data-error]').textContent='';}
 },true);
 const controls=row=>[...row.querySelectorAll(person(row)?'[name$="-work_mode"],[name$="-planning_position"],[name$="-activity"],[name$="-DELETE"]':'[name$="-actual_quantity"],[name$="-comment"],[name$="-actuation_type"],[name$="-actuation_reason"],[name$="-DELETE"]')];
 async function request(action,key){const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...scope,action,key})});const data=(response.headers.get('content-type')||'').includes('application/json')?await response.json():{};if(!response.ok){if(data.claim)claims.set(data.claim.key,data.claim);const error=new Error(data.error||'Не удалось проверить резерв.');error.status=response.status;error.retryable=response.status>=500||response.status===429;throw error;}return data;}
 function render(row){const repeated=duplicate(row),claim=claims.get(keyOf(row)),mine=!repeated&&claim?.state==='editing'&&claim.mine,bar=row.querySelector('[data-entry-claim]');if(!bar)return;
  const blocked=repeated||Boolean(claim&&!mine&&(claim.state==='editing'||!claim.own));
  controls(row).forEach(i=>{i.disabled=blocked;});row.querySelector('[data-person-remove],[data-work-remove]')?.toggleAttribute('disabled',blocked);editing(row).value=mine?'1':'0';token(row).value=mine?session:'';
  // Identity belongs to the reservation too: it cannot change under edited data.
  for(const name of person(row)?['employee','shift']:['project_work','work_item']){const input=field(row,name);if(input)input.disabled=blocked||Boolean(keyOf(row)&&(mine||row.dataset.entryDirty||person(row)&&field(row,'id')?.value));}
  const take=bar.querySelector('[data-take]'),release=bar.querySelector('[data-release]');take.hidden=true;take.disabled=repeated||!keyOf(row)||Boolean(claim&& !mine&& (claim.state==='editing'||!claim.own));release.hidden=!row.dataset.entryDirty||mine;release.textContent=row.dataset.entryDirty?'Отменить изменения':'Освободить';
  bar.querySelector('[data-label]').textContent=repeated?'Повтор':mine?'Ваш резерв':claim?.state==='editing'?'Резерв: '+claim.owner+(claim.own?' · другая вкладка':''):claim?.state==='saved'?(claim.own?'Внёс: '+claim.owner+' · можно исправить':'Уже учтено: '+claim.owner+(claim.other_object?' · другой объект':' · только просмотр')):row.dataset.entryDirty?'Восстанавливаем резерв…':keyOf(row)?'Запись свободна':'Сначала выберите '+(person(row)?'сотрудника и смену':'работу');
  if(person(row))bar.querySelector('[data-label]').title=bar.querySelector('[data-label]').textContent;
 }
 function capture(row){return [...row.querySelectorAll('input,select,textarea')].filter(i=>!['person_claim_token','person_editing','work_claim_token','work_editing'].some(n=>i.name.endsWith('-'+n))).map(i=>({name:i.name,value:i.value,checked:i.checked,type:i.type}));}
 function reset(row){const baseline=JSON.parse(row.dataset.entryOriginal);row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());for(const entry of baseline){if(entry.name.endsWith('-work_targets')){const i=document.createElement('input');i.type='hidden';i.name=entry.name;i.value=entry.value;row.append(i);}else{const i=[...row.querySelectorAll('[name]')].find(i=>i.name===entry.name);if(i){i.value=entry.value;if(entry.type==='checkbox')i.checked=entry.checked;}}}}
 function wire(){for(const row of rows()){if(row.dataset.entryWired)continue;row.dataset.entryWired='1';row.dataset.entryOriginal=JSON.stringify(capture(row));row.dataset.entryKey=keyOf(row);if(person(row)){row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;}
   if(!row.querySelector('[data-entry-claim]')){const bar=document.createElement('div');bar.dataset.entryClaim='';bar.className='work-claim-bar';
   for(const [tag,attr,text] of [['span','label',''],['button','take','Резерв'],['button','release','Освободить'],['span','error','']]){const el=document.createElement(tag);el.dataset[attr]='';el.textContent=text;if(tag==='button'){el.type='button';el.className='btn btn-sm btn-outline-primary';}else el.className=attr==='error'?'small text-danger':'small';bar.append(el);}row.prepend(bar);}
   if(form.dataset.productionBound==='1'&&editing(row)?.value==='1'&&(person(row)?field(row,'employee')?.value:field(row,'actual_quantity')?.value))row.dataset.entryDirty='1';render(row);
  }}
 function changed(){refreshPeople();rows().forEach(render);form.dispatchEvent(new Event('production-claims-change'));}
 form.querySelector('[data-person-take-list]')?.addEventListener('click',async event=>{
  const button=event.currentTarget,result=form.querySelector('[data-person-take-result]');button.disabled=true;busy=true;generation++;let taken=0,blocked=0;
  try{for(const row of rows().filter(person)){if(!keyOf(row)||field(row,'DELETE')?.checked)continue;if(duplicate(row)){blocked++;render(row);continue;}if(claims.get(keyOf(row))?.mine){taken++;continue;}try{const data=await request('take',keyOf(row));claims.set(keyOf(row),data.claim);taken++;}catch(error){blocked++;row.querySelector('[data-error]').textContent=error.message;}render(row);}form.dispatchEvent(new Event('production-autosave-all'));result.textContent=`Взято: ${taken}`+(blocked?` · Недоступно: ${blocked}. Проверьте сообщения у сотрудников.`:'');changed();lastActivity=Date.now();}finally{button.disabled=false;busy=false;}
 });
 form.addEventListener('click',async event=>{const take=event.target.closest('[data-take]'),release=event.target.closest('[data-release]');if(!take&&!release)return;const row=event.target.closest('[data-work-fact-row],[data-production-person]'),key=keyOf(row),error=row.querySelector('[data-error]');if(duplicate(row))return;error.textContent='';busy=true;generation++;
  try{if(take){const data=await request('take',key);claims.set(key,data.claim);if(person(row)&&!field(row,'id').value)row.dataset.entryDirty='1';}else{if(claims.get(key)?.mine)await request('release',key);reset(row);if(person(row)){row.dataset.acceptedEmployee=field(row,'employee').value;row.dataset.acceptedShift=field(row,'shift').value;}delete row.dataset.entryDirty;delete row.dataset.personDirty;delete row.dataset.autoPending;claims.delete(key);}render(row);changed();lastActivity=Date.now();}catch(failure){error.textContent=failure.message;render(row);}finally{busy=false;}
 });
 form.addEventListener('input',e=>{const row=e.target.closest('[data-work-fact-row],[data-production-person]');if(row&&controls(row).includes(e.target)&&!(e.target.name?.endsWith('-DELETE')&&!field(row,'id')?.value))row.dataset.entryDirty='1';lastActivity=Date.now();});
 form.addEventListener('change',e=>{wire();const row=e.target.closest('[data-work-fact-row],[data-production-person]');if(row){if(row.dataset.personDirty||controls(row).includes(e.target)&&!(e.target.name?.endsWith('-DELETE')&&!field(row,'id')?.value))row.dataset.entryDirty='1';const old=row.dataset.entryKey,key=keyOf(row);if(old!==key){if(claims.get(old)?.mine){generation++;request('release',old).catch(()=>{});claims.delete(old);}row.dataset.entryKey=key;}render(row);}refreshPeople();rows().forEach(render);lastActivity=Date.now();});
 async function poll(){if(document.hidden||busy)return;const version=generation;try{const response=await fetch(url+'?'+new URLSearchParams(scope),{credentials:'same-origin'});if(!response.ok)return;const data=await response.json();if(version!==generation)return;claims=new Map(data.claims.map(c=>[c.key,c]));if(data.report)form.dispatchEvent(new CustomEvent('production-report-update',{detail:data.report}));wire();rows().forEach(render);changed();}catch(error){/* Retry without clearing input. */}}
 form.productionValidatePeople=()=>{refreshPeople();const repeated=personRows().find(duplicate);if(repeated){repeated.querySelector('[data-error]').textContent=duplicateText;throw new Error(duplicateText);}};
 const acquiring=new Map();
 form.productionClaimRow=async row=>{
  if(duplicate(row))throw new Error(duplicateText);const key=keyOf(row);if(!key)return;
  if(!claims.get(key)?.mine){
   if(!acquiring.has(key)){generation++;acquiring.set(key,request('take',key).then(data=>{claims.set(key,data.claim);}).finally(()=>acquiring.delete(key)));}
   await acquiring.get(key);
  }
  render(row);row.dataset.entryDirty='1';changed();lastActivity=Date.now();
 };
 form.productionSetClaims=values=>{claims=new Map(values.map(c=>[c.key,c]));wire();changed();};
 form.productionKeyOf=keyOf;
 form.productionSession={scope,csrf};
 form.productionReleaseRow=async row=>{const key=keyOf(row);if(claims.get(key)?.mine){await request('release',key);claims.delete(key);}render(row);changed();};
 const reserve=event=>{
  if(form.productionApplying||form.dataset.resetRequired==='1')return;
  const row=event.target.closest('[data-work-fact-row],[data-production-person]');if(!row||!keyOf(row)||!event.target.name)return;
  if(controls(row).includes(event.target)||['employee','shift'].some(name=>event.target===field(row,name))){
   row.dataset.entryDirty='1';form.productionClaimRow(row).catch(error=>{row.querySelector('[data-error]').textContent=error.message;render(row);});
  }
 };
 form.addEventListener('input',reserve);form.addEventListener('change',reserve);
 form.addEventListener('production-autosave-committed',()=>{wire();refreshPeople();rows().forEach(render);changed();});
 form.addEventListener('submit',event=>{if(event.defaultPrevented)return;refreshPeople();const repeated=personRows().find(duplicate);if(repeated){event.preventDefault();form.querySelector('[data-step="2"]')?.click();repeated.querySelector('[data-error]').textContent=duplicateText;repeated.scrollIntoView({block:'center'});return;}const lost=rows().find(row=>!(field(row,'DELETE')?.checked&&!field(row,'id')?.value)&&(row.dataset.entryDirty||row.dataset.personDirty)&&!claims.get(keyOf(row))?.mine);if(lost){event.preventDefault();const panel=lost.closest('[data-entry-step]');form.querySelector(`[data-step="${panel.dataset.entryStep}"]`)?.click();let group=lost.closest('details');while(group){group.open=true;group=group.parentElement.closest('details');}lost.querySelector('[data-error]').textContent='Возьмите запись снова или отмените изменения. Введённые значения сохранены в форме.';lost.scrollIntoView({block:'center'});return;}
  // Disabled identity controls still have to be posted for owned rows.
  rows().forEach(row=>{if(claims.get(keyOf(row))?.mine){for(const name of person(row)?['employee','shift']:['project_work','work_item']){const input=field(row,name);if(input)input.disabled=false;}}});submitting=true;
 });
 window.addEventListener('beforeunload',e=>{if(!submitting&&rows().some(r=>r.dataset.entryDirty||r.dataset.personDirty)){e.preventDefault();e.returnValue='';}});
 form.querySelectorAll('[data-rows]').forEach(host=>new MutationObserver(wire).observe(host,{childList:true}));wire();refreshPeople();rows().forEach(render);
})();
