(() => {
 'use strict';
 const tabs=[...document.querySelectorAll('[data-housing-tab]')],panels=[...document.querySelectorAll('[data-housing-panel]')],form=document.querySelector('[data-movement-form]');let dirty=false,submitting=false;
 function show(name){panels.forEach(p=>{p.hidden=p.dataset.housingPanel!==name;});tabs.forEach(t=>t.setAttribute('aria-current',t.dataset.housingTab===name?'page':'false'));}
 tabs.forEach(t=>t.addEventListener('click',()=>show(t.dataset.housingTab)));
 show(form?.querySelector('.errorlist')||location.hash==='#movement'?'movement':'roster');
 if(!form)return;
 form.querySelectorAll('[data-personnel-searchable]').forEach(select=>{const search=document.createElement('input');search.type='search';search.className='form-control housing-select-search';search.placeholder='Поиск по ФИО, должности или табельному номеру';search.setAttribute('aria-label','Поиск: '+(select.labels[0]?.textContent||'сотрудники'));const count=document.createElement('div');count.className='housing-count';count.setAttribute('aria-live','polite');select.before(search);select.after(count);
  const update=()=>{count.textContent='Выбрано: '+select.selectedOptions.length;};search.addEventListener('input',()=>{const terms=search.value.toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);for(const option of select.options)option.hidden=!option.selected&&!terms.every(t=>option.textContent.toLocaleLowerCase('ru').includes(t));});select.addEventListener('change',update);update();
 });
 const kind=form.querySelector('[name=kind]'),target=form.querySelector('[name=target]'),employees=form.querySelector('[name=employees]');
 function fields(){target.closest('.app-form-field').hidden=kind.value!=='TRANSFER';employees.closest('.app-form-field').hidden=kind.value!=='ARRIVAL';if(kind.value!=='ARRIVAL')for(const o of employees.options)o.selected=false;}
 kind.addEventListener('change',fields);fields();
 form.addEventListener('change',()=>{dirty=true;});form.addEventListener('input',e=>{if(e.target.name)dirty=true;});form.addEventListener('submit',()=>{submitting=true;});window.addEventListener('beforeunload',e=>{if(dirty&&!submitting){e.preventDefault();e.returnValue='';}});
})();
