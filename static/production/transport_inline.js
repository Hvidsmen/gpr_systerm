(()=>{
 'use strict';const root=document.querySelector('[data-transport-board]');if(!root||root.dataset.editable!=='1')return;
 const token=root.querySelector('[name=csrfmiddlewaretoken]').value,locks=window.transportBoardLocks,session=locks?.session;
 const rows=[...root.querySelectorAll('[data-transport-row]')];let people=null,active=null,activeReservation=null;
 function updateCounts(row,result){
  const changes=new Map();function add(key,value){changes.set(key,(changes.get(key)||0)+value);}
  add(row.dataset.status||'EMPTY',-1);add(result.status||'EMPTY',1);add(row.dataset.condition||'UNKNOWN',-1);add(result.condition||'UNKNOWN',1);
  for(const [key,delta] of changes){const counter=document.querySelector('[data-transport-count="'+key+'"]');if(counter&&delta)counter.textContent=String(Number(counter.textContent)+delta);}
 }
 const request=async(url,data)=>{const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить.');return result;};
 function say(row,text,failed=false){row.querySelectorAll('[data-cell-save-status]').forEach(status=>{status.textContent=text;status.classList.toggle('text-danger',failed);});}
 async function acquire(row){
  if(active===row)return activeReservation;
  const previous=active,prior=activeReservation;active=row;
  const reservation=(async()=>{if(previous){await prior?.catch(()=>{});await locks.claim('release',[previous.dataset.transportRow]);}await locks.claim('acquire',[row.dataset.transportRow]);})();
  activeReservation=reservation;try{await reservation;}catch(error){if(active===row){active=null;activeReservation=null;}throw error;}
 }
 root.addEventListener('focusin',e=>{if(!e.target.closest('[data-detail-field],.search-select-shell'))return;const row=e.target.closest('[data-transport-row]');if(row){row.reservation=acquire(row).catch(error=>{active=null;say(row,error.message,true);throw error;});row.reservation.catch(()=>{});}});
 rows.forEach(row=>{row.signed=root.dataset.inlineSigned;row.queue=Promise.resolve();row.releaseTransport=async()=>{await row.queue;await row.reservation?.catch(()=>{});if(active===row){await locks.claim('release',[row.dataset.transportRow]);active=null;activeReservation=null;row.reservation=null;}};for(const field of row.querySelectorAll('[data-detail-field]'))field.dataset.saved=field.value;});
 root.addEventListener('change',e=>{const field=e.target.closest('[data-detail-field]'),row=field?.closest('[data-transport-row]');if(!row)return;
  const value=field.value;if(value===field.dataset.saved)return;
  row.queue=row.queue.catch(()=>{}).then(async()=>{row.classList.add('transport-cell-saving');say(row,'Сохранение…');try{
   await (row.reservation||acquire(row));
   const result=await request(root.dataset.inlineUrl,{camp:root.dataset.camp,date:root.dataset.date,equipment:row.dataset.transportRow,field:field.dataset.detailField,value,signed:row.signed,session});
   updateCounts(row,result);row.signed=result.signed;field.dataset.saved=value;row.dataset.condition=result.condition;row.dataset.status=result.status;if(active===row){active=null;activeReservation=null;}row.reservation=null;
   const condition=row.querySelector('[data-detail-field=condition]'),status=row.querySelector('[data-detail-field=status]'),target=row.querySelector('[data-detail-field=target]'),comment=row.querySelector('[data-detail-field=day_comment]');if(condition){condition.value=result.condition;condition.dataset.saved=condition.value;}
   if(status){status.value=result.status;status.disabled=false;status.dataset.saved=status.value;}
   if(target){target.disabled=false;target.value=result.target;target.dataset.saved=target.value;}
   if(comment)comment.disabled=false;
   row.querySelectorAll('[data-condition-label]').forEach(badge=>{badge.textContent=result.condition_label;badge.className='transport-condition condition-'+result.condition;});
   row.querySelectorAll('[data-transport-employees]').forEach(select=>select.disabled=result.status!=='WORKED');
   if(field.dataset.detailField==='repair_stage'){const label=row.querySelector('[data-repair-summary]');if(label)label.textContent=field.value?field.selectedOptions[0].textContent:'Этап не указан';}
   if(field.dataset.detailField==='request_stage'){const label=row.querySelector('[data-request-summary]');if(label)label.textContent='Заявка: '+(field.value?field.selectedOptions[0].textContent:'не указана');}
   row.classList.remove('transport-cell-failed');say(row,'Сохранено');
  }catch(error){field.value=field.dataset.saved;field.dispatchEvent(new Event('input',{bubbles:true}));row.classList.add('transport-cell-failed');say(row,error.message,true);}finally{row.classList.remove('transport-cell-saving');}});
 });
 // Employee choices are loaded once, when a picker is first opened, rather than
 // repeating the whole employee catalogue in every machine row's HTML.
 function loadPeople(e){const shell=e.target.closest('.search-select-shell'),select=shell?.previousElementSibling;if(select?.hasAttribute('data-transport-employees')&&!select.dataset.loaded){select.dataset.loaded='1';people??=fetch(root.dataset.employeeUrl).then(r=>{if(!r.ok)throw new Error('Не удалось загрузить сотрудников.');return r.json();});people.then(data=>{const current=select.value;for(const option of data.options)if(![...select.options].some(o=>o.value===String(option.id)))select.add(new Option(option.name,String(option.id)));select.value=current;}).catch(error=>{delete select.dataset.loaded;say(select.closest('tr'),error.message,true);});}}
 root.addEventListener('pointerdown',loadPeople,true);root.addEventListener('focusin',loadPeople,true);
 const statusDialog=root.querySelector('[data-status-dialog]'),statusForm=statusDialog.querySelector('form');let target=null;
 root.querySelectorAll('[data-status-kind]').forEach(select=>{const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-outline-primary';button.textContent='+';button.setAttribute('aria-label','Добавить значение в справочник');select.closest('.transport-detail-control').append(button);button.addEventListener('click',()=>{target=select;statusForm.reset();statusForm.querySelector('[data-status-error]').textContent='';statusDialog.showModal();});});
 root.querySelectorAll('[data-close-inline-dialog]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
 statusForm.addEventListener('submit',async e=>{e.preventDefault();try{const result=await request(root.dataset.statusUrl,{kind:target.dataset.statusKind,name:statusForm.elements.name.value});root.querySelectorAll('[data-status-kind="'+result.kind+'"]').forEach(select=>{if(![...select.options].some(o=>o.value===String(result.id)))select.add(new Option(result.name,String(result.id)));});target.value=String(result.id);statusDialog.close();target.dispatchEvent(new Event('change',{bubbles:true}));}catch(error){statusForm.querySelector('[data-status-error]').textContent=error.message;}});
 const machineDialog=root.querySelector('[data-new-machine-dialog]'),machineForm=machineDialog.querySelector('form');
 root.querySelector('[data-new-machine]')?.addEventListener('click',()=>machineDialog.showModal());
 const type=machineForm.elements['new-machine-type'],model=machineForm.elements['new-machine-model'],models=JSON.parse(document.getElementById('transport-new-models').textContent);
 type.addEventListener('change',()=>{model.replaceChildren(new Option('Без отдельной модели',''));for(const option of models)if(String(option.parent_id)===type.value)model.add(new Option(option.brand_model||option.name,String(option.id)));});
 machineForm.addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await Promise.all(rows.map(row=>row.queue));const response=await fetch(machineForm.action,{method:'POST',credentials:'same-origin',headers:{'Accept':'application/json','X-CSRFToken':token},body:new FormData(machineForm)});const result=await response.json();if(!response.ok)throw new Error(Object.values(result.errors||{}).flat().join(' ')||'Не удалось добавить машину.');machineDialog.close();location.reload();}catch(error){machineForm.querySelector('[data-new-machine-error]').textContent=error.message;}finally{button.disabled=false;}});
 setInterval(()=>{if(active&&!document.hidden)locks.claim('renew',[active.dataset.transportRow]).catch(error=>say(active,error.message,true));},60000);
 window.addEventListener('pagehide',()=>{if(!active)return;const data=new URLSearchParams({camp:root.dataset.camp,date:root.dataset.date,session,action:'release',csrfmiddlewaretoken:token});data.append('equipment',active.dataset.transportRow);navigator.sendBeacon(root.dataset.claimUrl,data);});
 window.addEventListener('beforeunload',e=>{if(rows.some(row=>row.classList.contains('transport-cell-saving'))){e.preventDefault();e.returnValue='';}});
})();
