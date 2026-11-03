/* Bulk people selection creates ordinary independently audited employee rows. */
(()=>{
 'use strict';const form=document.querySelector('[data-production-form]'),dialog=document.querySelector('[data-people-picker]'),source=document.getElementById('production-employee-locations');if(!form||!dialog||!source||form.dataset.readonly==='1')return;
 const employees=Object.entries(JSON.parse(source.textContent)).map(([id,info])=>({id,...info})).sort((a,b)=>a.group-b.group||a.name.localeCompare(b.name,'ru')||a.id.localeCompare(b.id));
 const host=form.querySelector('[data-rows="labor"]'),template=document.getElementById('empty-labor'),field=(row,name)=>row.querySelector(`[name$="-${name}"]`),rows=()=>[...host.querySelectorAll('[data-production-person]')];
 const list=dialog.querySelector('[data-people-list]'),search=dialog.querySelector('[data-people-search]'),location=dialog.querySelector('[data-people-location]'),shift=dialog.querySelector('[data-people-shift]'),position=dialog.querySelector('[data-people-position]'),apply=dialog.querySelector('[data-people-apply]'),error=dialog.querySelector('[data-people-error]'),result=form.querySelector('[data-person-bulk-result]');
 let options=[],busy=false,controls=[];const selected=new Set();
 const validShift=()=>[1,2].includes(Number(shift.value));
 function presentInShift(){const present=new Map();for(const row of rows())if(Number(field(row,'shift').value)===Number(shift.value)&&!field(row,'DELETE')?.checked&&field(row,'employee')?.value)present.set(field(row,'employee').value,row);return present;}
 function availability(id,present=presentInShift()){if(!validShift())return {available:false,label:'Выберите дневную или ночную смену'};const existing=present.get(id);if(existing)return {available:false,label:field(existing,'id').value?'Уже добавлен в эту смену':'Уже есть в форме — ещё не сохранён'};return form.productionEmployeeAvailability(id,shift.value);}
 function refresh(){
  if(busy)return;let visible=0,available=0;const present=presentInShift(),terms=search.value.toLocaleLowerCase('ru').trim().split(/\s+/).filter(Boolean);
  for(const item of options){const state=availability(item.person.id,present);item.checkbox.disabled=!state.available;if(!state.available&&validShift()){selected.delete(item.person.id);item.checkbox.checked=false;}if(item.status.textContent!==state.label)item.status.textContent=state.label;item.label.classList.toggle('people-picker-unavailable',!state.available);item.label.hidden=Boolean(location.value!==''&&String(item.person.group)!==location.value||!terms.every(term=>item.text.includes(term)));if(!item.label.hidden){visible++;if(state.available)available++;}}
  dialog.querySelector('[data-people-empty]').hidden=Boolean(visible);dialog.querySelector('[data-people-count]').textContent=`Выбрано: ${selected.size} · Доступно в списке: ${available}`;apply.disabled=!selected.size||!validShift();
 }
 function lock(value){if(value){controls=[...dialog.querySelectorAll('input,select,button')].filter(input=>!input.matches('[data-people-close]')).map(input=>[input,input.disabled]);controls.forEach(([input])=>input.disabled=true);}else{controls.forEach(([input,disabled])=>input.disabled=disabled);controls=[];}}
 async function open(target){if(busy)return;search.value='';location.value='';shift.value='1';error.textContent='';selected.clear();options=[];list.replaceChildren();const sample=template.content.querySelector('[name$="-planning_position"]');position.replaceChildren(...[...sample.options].map(option=>option.cloneNode(true)));position.value=target||'';
  for(const person of employees){const label=document.createElement('label'),checkbox=document.createElement('input'),body=document.createElement('span'),name=document.createElement('strong'),details=document.createElement('small'),where=document.createElement('small'),status=document.createElement('small');checkbox.type='checkbox';checkbox.value=person.id;checkbox.setAttribute('aria-label',person.name);label.dataset.employeeId=person.id;name.textContent=person.name;details.textContent=(person.number?'№ '+person.number+' · ':'')+(person.position_label||'Должность не указана');where.textContent=person.text;status.className='people-picker-status';body.append(name,details,where,status);label.append(checkbox,body);list.append(label);const text=[person.name,person.number,person.position_label,person.text].join(' ').toLocaleLowerCase('ru');options.push({person,label,checkbox,status,text});checkbox.addEventListener('change',()=>{if(checkbox.checked)selected.add(person.id);else selected.delete(person.id);error.textContent='';refresh();});}
  refresh();dialog.showModal();search.focus();await form.productionRefresh?.();refresh();
 }
 document.addEventListener('click',event=>{const global=event.target.closest('[data-person-bulk-open]'),target=event.target.closest('[data-person-bulk-position]');if(global||target)open(target?.dataset.personBulkPosition||'');});
 search.addEventListener('input',refresh);location.addEventListener('change',refresh);shift.addEventListener('input',()=>{error.textContent='';refresh();});shift.addEventListener('change',refresh);
 search.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault();});
 dialog.querySelector('[data-people-select]').addEventListener('click',()=>{options.filter(item=>!item.label.hidden&&!item.checkbox.disabled).forEach(item=>{selected.add(item.person.id);item.checkbox.checked=true;});refresh();});
 dialog.querySelector('[data-people-clear]').addEventListener('click',()=>{selected.clear();options.forEach(item=>item.checkbox.checked=false);refresh();});
 dialog.querySelectorAll('[data-people-close]').forEach(button=>button.addEventListener('click',()=>{if(!busy)dialog.close();}));dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 form.addEventListener('production-claims-change',()=>{if(dialog.open)refresh();});form.addEventListener('production-live-applied',()=>{if(dialog.open)refresh();});
 apply.addEventListener('click',async()=>{
  if(busy)return;refresh();if(!validShift()){error.textContent='Выберите дневную или ночную смену.';return;}const chosen=employees.filter(person=>selected.has(person.id));if(!chosen.length)return;
  const shiftValue=String(Number(shift.value)),positionValue=position.value,staged=[];let saving=false;busy=true;lock(true);error.textContent='';result.textContent='Сохраняем выбранных сотрудников. Можно продолжать работу с другими записями.';
  try{
   if(!await window.productionAutoSave.flushPeople())throw new Error('Сначала исправьте ошибки сохранения сотрудников в текущей форме.');
   if(Number(form.elements.namedItem('labor-TOTAL_FORMS').value)+chosen.length>2000)throw new Error('В форме можно сохранить до 2000 строк. Уменьшите выбор.');
   form.dataset.peopleBulkBusy='1';form.productionApplying=true;
   await form.productionClaimPeople(chosen.map(person=>({employee:person.id,shift:Number(shiftValue)})));
   const fragment=document.createDocumentFragment(),total=form.elements.namedItem('labor-TOTAL_FORMS');let index=Number(total.value);
   for(const person of chosen){const state=availability(person.id);if(!state.available)throw new Error(person.name+': '+state.label);const piece=document.createElement('template');piece.innerHTML=template.innerHTML.replaceAll('__prefix__',index++);const row=piece.content.firstElementChild;staged.push(row);field(row,'employee').value=person.id;field(row,'shift').value=shiftValue;field(row,'planning_position').value=positionValue;if(!field(row,'employee').value)throw new Error('Обновите страницу: список сотрудников изменился.');row.dataset.personDirty='1';row.dataset.entryDirty='1';fragment.append(row);}
   total.value=index;host.append(fragment);form.productionApplying=false;form.dispatchEvent(new Event('production-people-bulk-staged'));saving=true;

   const saved=await window.productionAutoSave.flushPeople();if(!saved)throw new Error('Сотрудники добавлены в форму, но не сохранены. Проверьте сообщения у строк и нажмите «Повторить сохранение».');
   if(staged.some(row=>!field(row,'id')?.value))throw new Error('Некоторые сотрудники не сохранены. Проверьте их строки перед повторным выбором.');
   result.textContent=`Добавлено: ${staged.length} · ${shiftValue==='2'?'Ночь':'День'}. Проверьте работы на вкладке «Связи».`;dialog.close();
  }catch(failure){
   if(!saving){for(const row of staged){try{await form.productionReleaseRow(row);}catch{}row.remove();}window.productionAutoSave.reindex();error.textContent=failure.message;}
   else{result.textContent=failure.message;dialog.close();}
  }finally{form.productionApplying=false;delete form.dataset.peopleBulkBusy;busy=false;lock(false);refresh();form.productionRefresh?.();}
 });
})();
