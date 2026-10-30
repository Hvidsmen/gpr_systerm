(()=>{
 'use strict';
 const context=document.querySelector('[data-fact-context]');if(!context)return;
 const selector=document.querySelector('[data-fact-selector]'),warning=document.querySelector('[data-fact-filter-warning]');
 const fingerprint=form=>JSON.stringify([...new FormData(form)].filter(([name])=>!['csrfmiddlewaretoken','signed','session'].includes(name)));
 if(selector){
  const original=fingerprint(selector),refresh=()=>{warning.hidden=fingerprint(selector)===original;};
  selector.addEventListener('input',refresh);selector.addEventListener('change',refresh);
  const date=selector.elements.namedItem('date');
  if(date){context.querySelector('[data-fact-shortcuts]').hidden=false;context.querySelectorAll('[data-fact-date]').forEach(button=>{if(button.dataset.factDate===context.querySelector('time').dateTime)button.setAttribute('aria-current','date');button.addEventListener('click',()=>{date.value=button.dataset.factDate;date.dispatchEvent(new Event('change',{bubbles:true}));selector.requestSubmit();});});}
 }
 const forms=[...document.querySelectorAll('[data-fact-entry]')];
 const box=document.querySelector('[data-fact-error-summary]'),list=box.querySelector('[data-fact-error-list]');let errorTimer,renderedErrors=null;
 const errorSelector='.errorlist,[data-error],.app-form-errors';
 function reveal(node){
  const step=node.closest('[data-entry-step]');if(step)step.closest('form').querySelector('[data-step="'+step.dataset.entryStep+'"]')?.click();
  const panel=node.closest('[data-housing-panel]');if(panel)document.querySelector('[data-housing-tab="'+panel.dataset.housingPanel+'"]')?.click();
  for(let p=node.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;
  const group=node.closest('.app-form-field,.production-work-field,.production-work-card,[data-production-person],.lodging-movement-fields>div')||node.parentElement;
  const input=group.querySelector('[aria-invalid=true]:not([type=hidden]):not(:disabled)')||group.querySelector('[name$="-actual_quantity"]:not(:disabled)')||group.querySelector('input:not([type=hidden]):not(:disabled),select:not(:disabled),textarea:not(:disabled)');
  const target=input||node;target.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});if(!target.hasAttribute('tabindex')&&!input)target.tabIndex=-1;target.focus({preventScroll:true});
 }
 function errors(){
  const entries=[],seen=new Set();
  for(const form of forms)for(const node of form.querySelectorAll(errorSelector)){
   const text=node.textContent.trim();if(!text||node.querySelector(errorSelector))continue;
   const dialog=node.closest('dialog');if(dialog&&!dialog.open)continue;
   const row=node.closest('[data-work-fact-row],[data-production-person]');if(row?.querySelector('[name$="-DELETE"]:checked'))continue;
   const group=node.closest('.app-form-field,.production-work-field,.lodging-movement-fields>div');const key=(group?.querySelector('[name]')?.name||row?.querySelector('[name$="-id"]')?.name||'')+'|'+text;if(seen.has(key))continue;seen.add(key);entries.push({node,text,row});
  }
  if(renderedErrors&&entries.length===renderedErrors.length&&entries.every((entry,i)=>entry.node===renderedErrors[i].node&&entry.text===renderedErrors[i].text))return;renderedErrors=entries;
  box.hidden=!entries.length;box.querySelector('[data-fact-error-title]').textContent='Нужно исправить: '+entries.length;list.replaceChildren();
  for(const {node,text,row} of entries.slice(0,30)){const li=document.createElement('li'),button=document.createElement('button'),name=row?.querySelector('[data-work-title]')?.textContent.trim();button.type='button';button.textContent=(name?name+': ':'')+text;button.addEventListener('click',()=>reveal(node));li.append(button);list.append(li);}
  if(entries.length>30){const li=document.createElement('li');li.textContent='Показаны первые 30 сообщений. После исправления список обновится.';list.append(li);}
  document.querySelectorAll('[data-fact-error-jump]').forEach(button=>{button.hidden=!entries.length;button.textContent='Показать ошибки · '+entries.length;});
 }
 const scheduleErrors=()=>{clearTimeout(errorTimer);errorTimer=setTimeout(errors,120);};
 for(const form of forms){new MutationObserver(scheduleErrors).observe(form,{childList:true,subtree:true,characterData:true});form.addEventListener('change',scheduleErrors);}
 document.querySelectorAll('[data-fact-error-jump]').forEach(button=>button.addEventListener('click',()=>{errors();box.scrollIntoView({block:'center'});list.querySelector('button')?.focus({preventScroll:true});}));errors();
 for(const form of document.querySelectorAll('[data-fact-manual]')){
  const original=fingerprint(form),state=document.createElement('span');state.className='fact-manual-state';state.setAttribute('role','status');state.dataset.factManualState='';let submitting=false;
  const submit=form.querySelector('button[type=submit],button:not([type])');if(!submit)continue;const actions=submit.closest('footer,.fact-save-actions,.d-flex')||submit.parentElement;actions.append(state);
  function refresh(){state.dataset.state=form.querySelector('.errorlist')?'error':fingerprint(form)!==original?'dirty':'clean';state.textContent=state.dataset.state==='error'?'Проверьте сообщения об ошибках':state.dataset.state==='dirty'?'Есть несохранённые изменения':'Изменений в форме нет';}
  form.addEventListener('input',refresh);form.addEventListener('change',refresh);refresh();
  form.addEventListener('submit',event=>{if(submitting){event.preventDefault();return;}queueMicrotask(()=>{if(!event.defaultPrevented){submitting=true;form.setAttribute('aria-busy','true');state.textContent='Отправка данных…';}});});
  window.addEventListener('pageshow',()=>{submitting=false;form.removeAttribute('aria-busy');refresh();});
  if(form.hasAttribute('data-fact-leave-guard'))window.addEventListener('beforeunload',event=>{if(!submitting&&fingerprint(form)!==original){event.preventDefault();event.returnValue='';}});
 }
})();
