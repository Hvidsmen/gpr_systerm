(()=>{'use strict';const panel=document.querySelector('[data-lodging-presence]');if(!panel)return;const mode=panel.querySelector('[data-presence-mode]'),viewers=panel.querySelector('[data-presence-viewers]'),csrf=panel.querySelector('[name=csrfmiddlewaretoken]').value,initialEditor=panel.dataset.editor==='1';let stopped=false;
 async function touch(){
  try{const response=await fetch(panel.dataset.url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({camp:panel.dataset.camp,session:panel.dataset.session,action:'touch'})}),result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось обновить режим доступа.');if(Boolean(result.editor)!==initialEditor){location.reload();return;}if(result.editor){mode.textContent='Вы редактируете это местонахождение.';viewers.hidden=false;viewers.textContent=result.viewers.length?' Сейчас смотрят: '+result.viewers.join(', ')+'.':' Сейчас никто не смотрит.';}else{mode.textContent=result.editor_name?'Только просмотр: местонахождение сейчас редактирует '+result.editor_name+'.':'Только просмотр: редактор местонахождения пока не открыт.';viewers.hidden=true;}}
  catch(error){mode.textContent=error.message;panel.classList.remove('alert-success');panel.classList.add('alert-warning');}
 }
 touch();const timer=setInterval(()=>{if(!stopped)touch();},25000);window.addEventListener('pagehide',()=>{stopped=true;clearInterval(timer);});
})();
(()=>{'use strict';const form=document.querySelector('[data-lodging-movement]');if(!form)return;const field=name=>form.elements.namedItem(name),kind=field('kind'),employee=field('employee'),building=field('building'),bed=field('bed'),day=field('date'),signed=field('signed'),session=field('session').value,csrf=field('csrfmiddlewaretoken').value,save=form.querySelector('[data-lodging-save]'),label=form.querySelector('[data-lodging-lock-label]'),roster=JSON.parse(document.getElementById('lodging-roster-data').textContent);let claimed=false,busy=false,posting=false,held=null,bedsLoading=false,bedsRequest=0;
 const payload=action=>({camp:form.dataset.camp,date:day.value,employee:employee.value,bed:['CHECKIN','MOVE'].includes(kind.value)?bed.value:'',session,action});
 async function request(action){const response=await fetch(form.dataset.claimUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(action==='release'&&held?{...held,action}:payload(action))});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить проживание.');return result;}
 function say(message,failed=false){label.textContent=message;label.toggleAttribute('data-failed',failed);}
 function setClaim(value){claimed=value;if(!value)signed.value='';}
 async function releaseClaim(){if(!claimed)return;busy=true;setClaim(false);try{await request('release');}catch(error){say(error.message,true);}finally{held=null;busy=false;}}
 function refresh(){const needsBed=['CHECKIN','MOVE'].includes(kind.value);form.querySelectorAll('[data-new-bed]').forEach(node=>node.hidden=!needsBed);save.disabled=busy||bedsLoading||!day.value||!employee.value||(needsBed&&!bed.value);}
 async function changed(){await releaseClaim();refresh();say('Изменения готовы к сохранению.');}
 function filterBeds(){const selectedBuilding=building.value;let visible=0;[...bed.options].forEach(option=>{if(!option.value)return;const shown=!selectedBuilding||option.dataset.building===selectedBuilding;option.hidden=!shown;option.disabled=!shown;if(shown)visible++;});if(bed.value&&bed.selectedOptions[0]?.disabled)bed.value='';bed.dispatchEvent(new Event('work-options-updated',{bubbles:true}));refresh();return visible;}
 async function loadBeds(){
  const requestId=++bedsRequest,selected=bed.value;bedsLoading=true;bed.disabled=true;refresh();
  try{const response=await fetch(form.dataset.bedsUrl+'?'+new URLSearchParams({camp:form.dataset.camp,date:day.value,session}),{credentials:'same-origin'}),result=await response.json();if(requestId!==bedsRequest)return;if(!response.ok)throw new Error(result.error||'Не удалось обновить свободные места.');
   const options=result.beds.map(row=>{const option=new Option(row.label,String(row.id));option.dataset.building=String(row.building_id);return option;});bed.replaceChildren(new Option('Выберите свободное место',''),...options);bed.value=[...bed.options].some(option=>option.value===selected)?selected:'';const visible=filterBeds();bed.dispatchEvent(new Event('change',{bubbles:true}));say(visible?(building.value?'Показаны свободные места выбранного вагона.':'Показаны все свободные места на выбранную дату.'):(building.value?'В выбранном вагоне свободных мест нет.':'Свободных мест на выбранную дату нет.'));
  }catch(error){if(requestId===bedsRequest){bed.replaceChildren(new Option('Не удалось загрузить места',''));say(error.message,true);}}
  finally{if(requestId===bedsRequest){bedsLoading=false;bed.disabled=false;refresh();}}
 }
 [employee,bed,kind].forEach(node=>node.addEventListener('change',changed));
 building.addEventListener('change',async()=>{filterBeds();await changed();say(building.value?'Показаны свободные места выбранного вагона.':'Показаны все свободные места.');});
 day.addEventListener('change',async()=>{await releaseClaim();if(!['CHECKIN','MOVE'].includes(kind.value)){refresh();say('Фактическая дата изменена. Выбранный сотрудник и сведения сохранены.');return;}if(day.value)loadBeds();else{bed.replaceChildren(new Option('Укажите дату',''));refresh();}});
 employee.addEventListener('change',()=>{const row=roster.find(r=>String(r.employee)===employee.value);if(row){field('planned_departure').value=row.planned_departure;field('keys_issued').checked=row.keys_issued;field('key_number').value=row.key_number||'';field('pass_number').value=row.pass_number;field('comment').value=row.comment;}else{field('planned_departure').value='';field('keys_issued').checked=false;field('key_number').value='';field('pass_number').value='';field('comment').value='';}});
 document.addEventListener('click',async event=>{const button=event.target.closest('[data-lodging-action]');if(!button)return;await releaseClaim();kind.value=button.dataset.lodgingAction;const employeeId=button.dataset.employee||'';if(employeeId&&!([...employee.options].some(option=>option.value===employeeId)))employee.add(new Option(button.dataset.employeeLabel||'Выбранный сотрудник',employeeId));employee.value=employeeId;building.value=button.dataset.building||'';filterBeds();bed.value=button.dataset.bed||'';employee.dispatchEvent(new Event('change',{bubbles:true}));kind.dispatchEvent(new Event('change',{bubbles:true}));building.dispatchEvent(new Event('work-options-updated',{bubbles:true}));bed.dispatchEvent(new Event('change',{bubbles:true}));const panel=document.getElementById('movement');panel.open=true;panel.scrollIntoView({block:'center',behavior:'smooth'});refresh();});
 document.querySelector('[data-open-lodging-movement]')?.addEventListener('click',()=>{document.getElementById('movement').open=true;});
 form.addEventListener('submit',async e=>{
  e.preventDefault();if(posting||busy)return;if(!form.reportValidity())return;
  busy=true;refresh();say('Проверяем сотрудника и место…');
  try{const result=await request('acquire');signed.value=result.signed;held=payload('acquire');setClaim(true);posting=true;say('Сохраняем…');HTMLFormElement.prototype.submit.call(form);}
  catch(error){setClaim(false);held=null;say(error.message,true);}
  finally{busy=false;if(!posting)refresh();}
 });
 const quickForm=document.querySelector('[data-quick-bed-form]'),quickPanel=document.querySelector('[data-lodging-quick-bed]');
 quickForm?.addEventListener('submit',async event=>{
  event.preventDefault();const button=quickPanel.querySelector('button[type=submit]'),message=quickPanel.querySelector('[data-quick-bed-message]');
  if(button.disabled)return;button.disabled=true;message.textContent='Создаём место…';message.classList.remove('text-danger');
  try{
   const data=new FormData(quickForm);data.set('date',day.value);
   const response=await fetch(quickForm.action,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':csrf},body:data});
   const result=await response.json();
   if(!response.ok)throw new Error(result.error||Object.values(result.errors||{}).flat().map(error=>error.message).join(' ')||'Не удалось создать место.');
   await releaseClaim();
   const buildingId=String(result.building.id),buildingLabel=result.building.label||result.building.name;
   if(![...building.options].some(option=>option.value===buildingId))building.add(new Option(buildingLabel,buildingId));building.value=buildingId;
   const place=new Option(result.label,String(result.id),false,true);place.dataset.building=buildingId;bed.add(place);filterBeds();bed.value=String(result.id);bed.dispatchEvent(new Event('change',{bubbles:true}));building.dispatchEvent(new Event('work-options-updated',{bubbles:true}));
   const buildings=quickForm.elements.namedItem('building');
   if(![...buildings.options].some(option=>option.value===buildingId))buildings.add(new Option(buildingLabel,buildingId));
   quickForm.reset();quickPanel.open=false;refresh();say('Новое место выбрано. Нажмите «Сохранить».');message.textContent='';
  }catch(error){message.textContent=error.message;message.classList.add('text-danger');}
  finally{button.disabled=false;}
 });
 setInterval(async()=>{if(!claimed||document.hidden)return;try{const result=await request('renew');signed.value=result.signed;}catch(error){setClaim(false);refresh();say(error.message,true);}},60000);
 window.addEventListener('pagehide',()=>{if(claimed&&!posting)fetch(form.dataset.claimUrl,{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...held,action:'release'})}).catch(()=>{});});
 window.addEventListener('beforeunload',e=>{if(claimed&&!posting){e.preventDefault();e.returnValue='';}});filterBeds();refresh();
})();
(()=>{'use strict';
 async function load(panel,url=panel.dataset.lazyUrl){
  if(panel.dataset.lazyLoaded==='1'||panel.dataset.lazyLoading==='1'||!url)return;
  const host=panel.querySelector('[data-lazy-content]');panel.dataset.lazyLoading='1';host.innerHTML='<p class="small text-muted m-3">Загрузка…</p>';
  try{const response=await fetch(url,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});if(!response.ok)throw new Error('Не удалось загрузить данные.');host.innerHTML=await response.text();panel.dataset.lazyLoaded='1';}
  catch(error){host.innerHTML='<p class="text-danger m-3">'+error.message+' Повторно закройте и откройте раздел.</p>';}
  finally{delete panel.dataset.lazyLoading;}
 }
 document.querySelectorAll('details[data-lazy-url]').forEach(panel=>panel.addEventListener('toggle',()=>{if(panel.open)load(panel);}));
 document.addEventListener('click',async event=>{const button=event.target.closest('[data-lodging-history-more]');if(!button)return;button.disabled=true;const panel=button.closest('details'),host=panel.querySelector('[data-lazy-content]');try{const response=await fetch(button.dataset.url,{credentials:'same-origin'});if(!response.ok)throw new Error('Не удалось загрузить историю.');const wrapper=document.createElement('div');wrapper.innerHTML=await response.text();const rows=wrapper.querySelectorAll('tbody tr'),body=host.querySelector('tbody');rows.forEach(row=>body.append(row));button.replaceWith(wrapper.querySelector('[data-lodging-history-more]')||document.createTextNode(''));}catch(error){button.disabled=false;button.textContent=error.message+' Повторить';}});
 if(location.hash==='#history'){const history=document.getElementById('history');if(history){history.open=true;load(history);}}
})();
