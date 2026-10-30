(() => {
 'use strict';
 const tabs=[...document.querySelectorAll('[data-housing-tab]')],panels=[...document.querySelectorAll('[data-housing-panel]')],form=document.querySelector('[data-movement-form]');let dirty=false,submitting=false;
 function show(name){if(name==='calendar')window.loadRotationCalendar?.();panels.forEach(p=>{p.hidden=p.dataset.housingPanel!==name;});tabs.forEach(t=>t.setAttribute('aria-current',t.dataset.housingTab===name?'page':'false'));}
 function linkedTab(){const name=location.hash.slice(1);return panels.some(p=>p.dataset.housingPanel===name)?name:'roster';}
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
 if(!form)return;
 form.querySelectorAll('[data-personnel-searchable]').forEach(select=>{const count=document.createElement('div');count.className='housing-count';count.setAttribute('aria-live','polite');select.after(count);const update=()=>{count.textContent='Выбрано: '+select.selectedOptions.length;};select.addEventListener('change',update);update();});
 const kind=form.querySelector('[name=kind]'),target=form.querySelector('[name=target]'),employees=form.querySelector('[name=employees]'),destination=form.querySelector('[name=destination]'),camp=form.querySelector('[name=camp]');
 const presentByCamp=JSON.parse(employees.dataset.presentByCamp||'{}');
 const selectedHost=form.querySelector('[data-movement-selected]');
 function selectedPeople(){
  selectedHost.replaceChildren();const selected=[...employees.selectedOptions];const title=document.createElement('strong');title.textContent=selected.length?'Сотрудники в действии: '+selected.length:'Добавьте сотрудников через список выше';selectedHost.append(title);
  const list=document.createElement('ul');list.className='movement-selected-list';for(const option of selected){const item=document.createElement('li');const name=document.createElement('span');name.textContent=option.textContent;item.append(name);if(form.dataset.personalMovement!=='1'){const remove=document.createElement('button');remove.type='button';remove.className='btn btn-sm btn-outline-secondary';remove.textContent='Убрать';remove.addEventListener('click',()=>{option.selected=false;employees.dispatchEvent(new Event('change',{bubbles:true}));});item.append(remove);}list.append(item);}selectedHost.append(list);
 }
 employees.addEventListener('change',selectedPeople);selectedPeople();
 function fields(){
  destination.closest('.app-form-field').hidden=kind.value!=='DEPARTURE';
  target.closest('.app-form-field').hidden=!(kind.value==='TRANSFER'||kind.value==='DEPARTURE'&&destination.value==='CAMP');
  const eligible=new Set((kind.value==='ARRIVAL'?employees.dataset.activeEmployees:(presentByCamp[camp.value]||[]).join(',')).split(','));
  for(const option of employees.options){option.disabled=!eligible.has(option.value);option.hidden=option.disabled;if(option.disabled&&form.dataset.personalMovement!=='1')option.selected=false;}
  for(const option of target.options){option.disabled=!!option.value&&option.value===camp.value;if(option.disabled)option.selected=false;}
  employees.dispatchEvent(new Event('change',{bubbles:true}));
 }
 kind.addEventListener('change',fields);destination.addEventListener('change',fields);camp.addEventListener('change',fields);fields();
 form.addEventListener('change',()=>{dirty=true;});form.addEventListener('input',e=>{if(e.target.name)dirty=true;});form.addEventListener('submit',()=>{submitting=true;});window.addEventListener('beforeunload',e=>{if(dirty&&!submitting){e.preventDefault();e.returnValue='';}});
})();
