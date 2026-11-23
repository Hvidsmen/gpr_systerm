(() => {
 'use strict';
 const legacy=document.querySelector('[data-movement-destination]');
 if(legacy&&['#movement','#events'].includes(location.hash)){location.replace(legacy.dataset.movementDestination+location.search+location.hash);return;}
 const tabs=[...document.querySelectorAll('[data-housing-tab]')],panels=[...document.querySelectorAll('[data-housing-panel]')],form=document.querySelector('[data-movement-form]');let dirty=false,submitting=false;
 function show(name){if(name==='calendar')window.loadRotationCalendar?.();panels.forEach(p=>{p.hidden=p.dataset.housingPanel!==name;});tabs.forEach(t=>t.setAttribute('aria-current',t.dataset.housingTab===name?'page':'false'));}
 function linkedTab(){const name=location.hash.slice(1);return panels.some(p=>p.dataset.housingPanel===name)?name:(panels[0]?.dataset.housingPanel||'roster');}
 tabs.forEach(t=>t.addEventListener('click',()=>{show(t.dataset.housingTab);history.replaceState(null,'','#'+t.dataset.housingTab);}));
 window.addEventListener('hashchange',()=>show(linkedTab()));
 show(form?.querySelector('.errorlist')?'movement':linkedTab());
 document.querySelectorAll('[data-plan-month-toggle]').forEach(button=>button.addEventListener('click',event=>{
  event.preventDefault();event.stopPropagation();
  const detail=document.getElementById(button.getAttribute('aria-controls'));if(!detail)return;
  detail.hidden=!detail.hidden;button.setAttribute('aria-expanded',String(!detail.hidden));
 }));
 const filter=document.querySelector('[data-rotation-filter]');
 filter?.querySelector('[name=state]')?.addEventListener('change',()=>filter.requestSubmit());
 if(filter){
  const project=filter.querySelector('[name=construction_object]'),wjg=filter.querySelector('[name=camps]'),mapNode=document.getElementById('rotation-project-camps');
  if(project&&wjg&&mapNode){
   let projectCamps={};try{projectCamps=JSON.parse(mapNode.textContent||'{}');}catch{}
   const syncWjg=selectAll=>{const allowed=new Set((projectCamps[project.value]||[]).map(String));for(const option of wjg.options){const available=allowed.has(option.value);option.hidden=!available;option.disabled=!available;if(selectAll)option.selected=available;else if(!available)option.selected=false;}wjg.dispatchEvent(new Event('change',{bubbles:true}));};
   project.addEventListener('change',()=>syncWjg(true));syncWjg(false);
  }
  const scrollKey='personnel:rotation-filter-position';
  filter.addEventListener('submit',()=>{
   const active=tabs.find(tab=>tab.getAttribute('aria-current')==='page');
   filter.action=location.pathname+'#'+(active?.dataset.housingTab||'roster');
   const target=new URL(filter.action,location.href);target.search=new URLSearchParams(new FormData(filter)).toString();
   try{sessionStorage.setItem(scrollKey,JSON.stringify({url:target.href,top:scrollY,at:Date.now()}));}catch{}
  });
  try{const saved=JSON.parse(sessionStorage.getItem(scrollKey)||'null');sessionStorage.removeItem(scrollKey);if(saved&&saved.url===location.href&&Date.now()-saved.at<60000)requestAnimationFrame(()=>requestAnimationFrame(()=>scrollTo(0,saved.top)));}catch{}
  const anchor=document.querySelector('[data-rotation-filter-anchor]'),expand=filter.querySelector('[data-rotation-filter-expand]'),disclosure=filter.querySelector('[data-rotation-filter-disclosure]');
  if(disclosure){
   try{if(disclosure.dataset.hasErrors!=='1'&&localStorage.getItem(disclosure.dataset.storageKey)==='closed')disclosure.open=false;}catch{}
   const syncDisclosure=()=>{expand?.setAttribute('aria-expanded',String(disclosure.open));try{localStorage.setItem(disclosure.dataset.storageKey,disclosure.open?'open':'closed');}catch{}};
   disclosure.addEventListener('toggle',syncDisclosure);syncDisclosure();
  }
  const offset=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--navbar-height'))||48;
  if(anchor&&'IntersectionObserver' in window)new IntersectionObserver(entries=>{const entry=entries[0];filter.classList.toggle('is-pinned',!entry.isIntersecting&&entry.boundingClientRect.top<offset);},{rootMargin:`-${offset}px 0px 0px 0px`}).observe(anchor);
 }
 document.querySelectorAll('[data-recommendation-select-all]').forEach(toggle=>{const recommendationForm=toggle.closest('form'),rows=[...recommendationForm.querySelectorAll('input[name="recommendations"]')];const sync=()=>{const selected=rows.filter(input=>input.checked).length;toggle.checked=Boolean(rows.length)&&selected===rows.length;toggle.indeterminate=selected>0&&selected<rows.length;};toggle.addEventListener('change',()=>{rows.forEach(input=>input.checked=toggle.checked);sync();});rows.forEach(input=>input.addEventListener('change',sync));sync();});
 if(!form)return;
 form.querySelectorAll('[data-personnel-searchable]').forEach(select=>{const count=document.createElement('div');count.className='housing-count';count.setAttribute('aria-live','polite');select.after(count);const update=()=>{count.textContent='Выбрано: '+select.selectedOptions.length;};select.addEventListener('change',update);update();});
 const kind=form.querySelector('[name=kind]'),date=form.querySelector('[name=date]'),target=form.querySelector('[name=target]'),employees=form.querySelector('[name=employees]'),camp=form.querySelector('[name=camp]'),direct=form.querySelector('[name=direct_fact]'),submit=form.querySelector('[type=submit]');
 const presentByCamp=JSON.parse(employees.dataset.presentByCamp||'{}');
 const baseCities=JSON.parse(employees.dataset.baseCities||'[]');
 const departureDetails=new Map(Object.entries(JSON.parse(employees.dataset.departureDetails||'{}')));
 const selectedHost=form.querySelector('[data-movement-selected]');
 function selectedPeople(){
  selectedHost.replaceChildren();const selected=[...employees.selectedOptions];const title=document.createElement('strong');title.textContent=selected.length?'Сотрудники в действии: '+selected.length:'Добавьте сотрудников через список выше';selectedHost.append(title);
  const list=document.createElement('ul');list.className='movement-selected-list';for(const option of selected){const item=document.createElement('li');const name=document.createElement('span');name.className='movement-selected-person';name.textContent=option.textContent;item.append(name);if(kind.value==='DEPARTURE'){const saved=departureDetails.get(option.value)||{};const controls=document.createElement('div');controls.className='movement-departure-controls';const reason=document.createElement('select');reason.name='departure_destination_'+option.value;reason.className='form-select form-select-sm';reason.required=true;reason.setAttribute('aria-label','Причина выезда: '+option.textContent);for(const [value,label] of [['REST','На межвахтовый отдых'],['DISMISSAL','Увольнение']]){const choice=document.createElement('option');choice.value=value;choice.textContent=label;choice.selected=(saved.destination||'REST')===value;reason.append(choice);}const city=document.createElement('select');city.name='base_city_'+option.value;city.className='form-select form-select-sm';city.setAttribute('aria-label','Базовый город: '+option.textContent);city.append(new Option('Выберите базовый город',''));for(const row of baseCities)city.append(new Option(row.label,row.value));city.append(new Option('Добавить новый город…','__new__'));city.value=saved.city||(!baseCities.length?'__new__':'');const newCity=document.createElement('input');newCity.type='text';newCity.name='new_base_city_'+option.value;newCity.className='form-control form-control-sm movement-new-city';newCity.maxLength=150;newCity.placeholder='Название нового базового города';newCity.value=saved.new_city||'';newCity.setAttribute('aria-label','Новый базовый город: '+option.textContent);const syncCity=()=>{const requestMode=!(direct&&direct.checked);city.required=requestMode;newCity.hidden=city.value!=='__new__';newCity.required=requestMode&&city.value==='__new__';departureDetails.set(option.value,{destination:reason.value,city:city.value,new_city:newCity.value});};reason.addEventListener('change',syncCity);city.addEventListener('change',syncCity);newCity.addEventListener('input',syncCity);controls.append(reason,city,newCity);syncCity();item.append(controls);}if(form.dataset.personalMovement!=='1'){const remove=document.createElement('button');remove.type='button';remove.className='btn btn-sm btn-outline-secondary';remove.textContent='Убрать';remove.addEventListener('click',()=>{option.selected=false;employees.dispatchEvent(new Event('change',{bubbles:true}));});item.append(remove);}list.append(item);}selectedHost.append(list);
 }
 employees.addEventListener('change',selectedPeople);selectedPeople();
 function fields(){
  target.closest('.app-form-field').hidden=kind.value!=='TRANSFER';
  const suffix={ARRIVAL:'заезда',DEPARTURE:'выезда',TRANSFER:'перевода'}[kind.value]||'поездки';if(date?.labels?.[0])date.labels[0].textContent=((direct&&direct.checked)?'Фактическая':'Плановая')+' дата '+suffix;
  const eligible=new Set((kind.value==='ARRIVAL'?employees.dataset.activeEmployees:(presentByCamp[camp.value]||[]).join(',')).split(','));
  for(const option of employees.options){option.disabled=!eligible.has(option.value);option.hidden=option.disabled;if(option.disabled&&form.dataset.personalMovement!=='1')option.selected=false;}
  for(const option of target.options){option.disabled=!!option.value&&option.value===camp.value;if(option.disabled)option.selected=false;}
  employees.dispatchEvent(new Event('change',{bubbles:true}));
 }
 kind.addEventListener('change',fields);camp.addEventListener('change',fields);fields();
 function submitLabel(){if(submit)submit.textContent=direct&&direct.checked?'Провести факт':'Создать заявки';}
 if(direct)direct.addEventListener('change',()=>{submitLabel();fields();});submitLabel();
 form.addEventListener('change',()=>{dirty=true;});form.addEventListener('input',e=>{if(e.target.name)dirty=true;});form.addEventListener('submit',()=>{submitting=true;});window.addEventListener('beforeunload',e=>{if(dirty&&!submitting){e.preventDefault();e.returnValue='';}});
})();
