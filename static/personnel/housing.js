(() => {
 'use strict';
 const tabs=[...document.querySelectorAll('[data-housing-tab]')],panels=[...document.querySelectorAll('[data-housing-panel]')],form=document.querySelector('[data-movement-form]');let dirty=false,submitting=false;
 function show(name){panels.forEach(p=>{p.hidden=p.dataset.housingPanel!==name;});tabs.forEach(t=>t.setAttribute('aria-current',t.dataset.housingTab===name?'page':'false'));}
 function linkedTab(){const name=location.hash.slice(1);return panels.some(p=>p.dataset.housingPanel===name)?name:'roster';}
 tabs.forEach(t=>t.addEventListener('click',()=>{show(t.dataset.housingTab);history.replaceState(null,'','#'+t.dataset.housingTab);}));
 window.addEventListener('hashchange',()=>show(linkedTab()));
 show(form?.querySelector('.errorlist')?'movement':linkedTab());
 const filter=document.querySelector('[data-rotation-filter]');
 filter?.querySelectorAll('input[name=state]').forEach(input=>input.addEventListener('change',()=>filter.requestSubmit()));
 if(filter){
  const scrollKey='personnel:rotation-filter-position';
  filter.addEventListener('submit',()=>{
   const active=tabs.find(tab=>tab.getAttribute('aria-current')==='page');
   filter.action=location.pathname+'#'+(active?.dataset.housingTab||'roster');
   const target=new URL(filter.action,location.href);target.search=new URLSearchParams(new FormData(filter)).toString();
   try{sessionStorage.setItem(scrollKey,JSON.stringify({url:target.href,top:scrollY,at:Date.now()}));}catch{}
  });
  try{const saved=JSON.parse(sessionStorage.getItem(scrollKey)||'null');sessionStorage.removeItem(scrollKey);if(saved&&saved.url===location.href&&Date.now()-saved.at<60000)requestAnimationFrame(()=>requestAnimationFrame(()=>scrollTo(0,saved.top)));}catch{}
  const anchor=document.querySelector('[data-rotation-filter-anchor]'),expand=filter.querySelector('[data-rotation-filter-expand]');
  expand?.addEventListener('click',()=>{const opened=filter.classList.toggle('is-expanded');expand.setAttribute('aria-expanded',String(opened));});
  const offset=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--navbar-height'))||48;
  if(anchor&&'IntersectionObserver' in window)new IntersectionObserver(entries=>{const entry=entries[0];filter.classList.toggle('is-pinned',!entry.isIntersecting&&entry.boundingClientRect.top<offset);},{rootMargin:`-${offset}px 0px 0px 0px`}).observe(anchor);
 }
 if(!form)return;
 form.querySelectorAll('[data-personnel-searchable]').forEach(select=>{const count=document.createElement('div');count.className='housing-count';count.setAttribute('aria-live','polite');select.after(count);const update=()=>{count.textContent='Выбрано: '+select.selectedOptions.length;};select.addEventListener('change',update);update();});
 const kind=form.querySelector('[name=kind]'),target=form.querySelector('[name=target]'),employees=form.querySelector('[name=employees]'),destination=form.querySelector('[name=destination]');
 function fields(){
  destination.closest('.app-form-field').hidden=kind.value!=='DEPARTURE';
  target.closest('.app-form-field').hidden=!(kind.value==='TRANSFER'||kind.value==='DEPARTURE'&&destination.value==='CAMP');
  const eligible=new Set((kind.value==='ARRIVAL'?employees.dataset.activeEmployees:employees.dataset.presentEmployees).split(','));
  for(const option of employees.options){option.disabled=!eligible.has(option.value);option.hidden=option.disabled;if(option.disabled)option.selected=false;}
  employees.dispatchEvent(new Event('change',{bubbles:true}));
 }
 kind.addEventListener('change',fields);destination.addEventListener('change',fields);fields();
 form.addEventListener('change',()=>{dirty=true;});form.addEventListener('input',e=>{if(e.target.name)dirty=true;});form.addEventListener('submit',()=>{submitting=true;});window.addEventListener('beforeunload',e=>{if(dirty&&!submitting){e.preventDefault();e.returnValue='';}});
})();
