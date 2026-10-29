(()=>{'use strict';const form=document.querySelector('[data-lodging-movement]');if(!form)return;const field=name=>form.elements.namedItem(name),kind=field('kind'),employee=field('employee'),bed=field('bed'),day=field('date'),signed=field('signed'),session=field('session').value,csrf=field('csrfmiddlewaretoken').value,take=form.querySelector('[data-lodging-take]'),release=form.querySelector('[data-lodging-release]'),save=form.querySelector('[data-lodging-save]'),details=form.querySelector('[data-lodging-details]'),label=form.querySelector('[data-lodging-lock-label]'),roster=JSON.parse(document.getElementById('lodging-roster-data').textContent);let claimed=false,busy=false,posting=false,held=null,bedsLoading=false,bedsRequest=0;
 const payload=action=>({camp:form.dataset.camp,date:day.value,employee:employee.value,bed:['CHECKIN','MOVE'].includes(kind.value)?bed.value:'',session,action});
 async function request(action){const response=await fetch(form.dataset.claimUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(action==='release'&&held?{...held,action}:payload(action))});const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось начать оформление.');return result;}
 function say(message,failed=false){label.textContent=message;label.toggleAttribute('data-failed',failed);}
 function setClaim(value){claimed=value;save.disabled=!value;details.disabled=!value;release.hidden=!value;take.disabled=value;if(!value)signed.value='';}
 async function releaseClaim(){if(!claimed)return;busy=true;setClaim(false);try{await request('release');}catch(error){say(error.message,true);}finally{held=null;busy=false;}}
 function refresh(){const needsBed=['CHECKIN','MOVE'].includes(kind.value);form.querySelector('[data-new-bed]').hidden=!needsBed;take.disabled=busy||bedsLoading||!employee.value||(needsBed&&!bed.value)||claimed;}
 async function changed(){await releaseClaim();refresh();say('Выбор изменён. Начните оформление заново.');}
 async function loadBeds(){
  const requestId=++bedsRequest,selected=bed.value;bedsLoading=true;bed.disabled=true;refresh();
  try{const response=await fetch(form.dataset.bedsUrl+'?'+new URLSearchParams({camp:form.dataset.camp,date:day.value,session}),{credentials:'same-origin'}),result=await response.json();if(requestId!==bedsRequest)return;if(!response.ok)throw new Error(result.error||'Не удалось обновить свободные места.');
   bed.replaceChildren(new Option('Выберите свободное место',''),...result.beds.map(row=>new Option(row.label,String(row.id))));bed.value=[...bed.options].some(option=>option.value===selected)?selected:'';bed.dispatchEvent(new Event('change',{bubbles:true}));say(result.beds.length?'Показаны свободные места на выбранную дату.':'Свободных мест на выбранную дату нет.');
  }catch(error){if(requestId===bedsRequest){bed.replaceChildren(new Option('Не удалось загрузить места',''));say(error.message,true);}}
  finally{if(requestId===bedsRequest){bedsLoading=false;bed.disabled=false;refresh();}}
 }
 [employee,bed,kind].forEach(node=>node.addEventListener('change',changed));
 day.addEventListener('change',async()=>{await releaseClaim();if(day.value)loadBeds();else{bed.replaceChildren(new Option('Укажите дату',''));refresh();}});
 employee.addEventListener('change',()=>{const row=roster.find(r=>String(r.employee)===employee.value);if(row){field('planned_departure').value=row.planned_departure;field('keys_issued').checked=row.keys_issued;field('key_number').value=row.key_number||'';field('pass_number').value=row.pass_number;field('comment').value=row.comment;}else{field('planned_departure').value='';field('keys_issued').checked=false;field('key_number').value='';field('pass_number').value='';field('comment').value='';}});
 take.addEventListener('click',async()=>{if(busy)return;busy=true;take.disabled=true;[kind,employee,bed,day].forEach(node=>node.disabled=true);say('Резервируем сотрудника и место…');try{const result=await request('acquire');signed.value=result.signed;held=payload('acquire');setClaim(true);say('Вы оформляете сотрудника и место. Резерв продлевается, пока страница открыта.');}catch(error){setClaim(false);say(error.message,true);refresh();}finally{busy=false;[kind,employee,bed,day].forEach(node=>node.disabled=false);refresh();}});
 release.addEventListener('click',async()=>{await releaseClaim();refresh();say('Оформление освобождено.');});
 document.querySelectorAll('[data-lodging-action]').forEach(button=>button.addEventListener('click',async()=>{await releaseClaim();kind.value=button.dataset.lodgingAction;employee.value=button.dataset.employee||'';bed.value=button.dataset.bed||'';employee.dispatchEvent(new Event('change',{bubbles:true}));kind.dispatchEvent(new Event('change',{bubbles:true}));bed.dispatchEvent(new Event('change',{bubbles:true}));const panel=document.getElementById('movement');panel.open=true;panel.scrollIntoView({block:'center',behavior:'smooth'});refresh();}));
 document.querySelector('[data-open-lodging-movement]')?.addEventListener('click',()=>{document.getElementById('movement').open=true;});
 form.addEventListener('submit',e=>{if(!claimed||!signed.value){e.preventDefault();say('Сначала начните оформление сотрудника и места.',true);return;}posting=true;save.disabled=true;});
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
   bed.add(new Option(result.label,String(result.id),false,true));bed.dispatchEvent(new Event('change',{bubbles:true}));
   const buildings=quickForm.elements.namedItem('building');
   if(![...buildings.options].some(option=>option.value===String(result.building.id)))buildings.add(new Option(result.building.name,String(result.building.id)));
   quickForm.reset();quickPanel.open=false;refresh();say('Новое место выбрано. Начните оформление сотрудника и места.');message.textContent='';
  }catch(error){message.textContent=error.message;message.classList.add('text-danger');}
  finally{button.disabled=false;}
 });
 setInterval(async()=>{if(!claimed||document.hidden)return;try{const result=await request('renew');signed.value=result.signed;}catch(error){setClaim(false);refresh();say(error.message,true);}},60000);
 window.addEventListener('pagehide',()=>{if(claimed&&!posting)fetch(form.dataset.claimUrl,{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...held,action:'release'})}).catch(()=>{});});
 window.addEventListener('beforeunload',e=>{if(claimed&&!posting){e.preventDefault();e.returnValue='';}});refresh();
})();
