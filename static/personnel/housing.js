(() => {
 'use strict';
 const tabs=[...document.querySelectorAll('[data-housing-tab]')],panels=[...document.querySelectorAll('[data-housing-panel]')],form=document.querySelector('[data-movement-form]');let dirty=false,submitting=false;
 function show(name){panels.forEach(p=>{p.hidden=p.dataset.housingPanel!==name;});tabs.forEach(t=>t.setAttribute('aria-current',t.dataset.housingTab===name?'page':'false'));}
 tabs.forEach(t=>t.addEventListener('click',()=>show(t.dataset.housingTab)));
 show(form?.querySelector('.errorlist')||location.hash==='#movement'?'movement':'roster');
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
