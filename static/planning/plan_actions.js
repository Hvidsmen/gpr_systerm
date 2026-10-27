(() => {
 document.querySelectorAll('[popovertarget]').forEach(button=>button.addEventListener('click',()=>{
   const menu=document.getElementById(button.getAttribute('popovertarget'));if(!menu)return;
   const rect=button.getBoundingClientRect();menu.style.position='fixed';menu.style.inset='auto';menu.style.top=Math.min(rect.bottom+6,window.innerHeight-180)+'px';menu.style.right=Math.max(12,window.innerWidth-rect.right)+'px';
 }));
 const header=document.querySelector('[data-plan-action-header]');if(!header)return;
 if(window.ResizeObserver)new ResizeObserver(()=>document.documentElement.style.setProperty('--plan-action-height',header.getBoundingClientRect().height+'px')).observe(header);
 const allocation=document.getElementById('allocation-editor')||document.getElementById('object-work-editor');
 const status=header.querySelector('[data-plan-save-status]')||document.createElement('span');
 if(!status.isConnected){status.className='plan-save-status';status.dataset.planSaveStatus='';status.role='status';status.setAttribute('aria-live','polite');header.append(status);}
 const warning=document.createElement('p');warning.className='small text-danger mb-0 mt-1';warning.hidden=true;header.append(warning);
 let resourceState='clean',planSaving=false;
 const initialError=status.dataset.initialState==='error';
 function update(){
   const planDirty=allocation?.dataset.dirty==='true';
   if(planSaving){status.textContent='Сохранение плана…';status.dataset.state='saving';}
   else if(resourceState==='saving'){status.textContent='Сохранение ресурса…'+(planDirty?' · В плане есть изменения':'');status.dataset.state='saving';}
   else if(resourceState==='error'){status.textContent='Ресурс не сохранён'+(planDirty?' · В плане есть изменения':'');status.dataset.state='error';}
   else if(planDirty||resourceState==='dirty'){status.textContent=resourceState==='saved'&&planDirty?'Ресурс сохранён · Есть изменения плана':planDirty&&resourceState==='dirty'?'Есть изменения плана и ресурса':planDirty?'Есть изменения плана':'Есть изменения ресурса';status.dataset.state='dirty';}
   else if(initialError){status.textContent='План не сохранён — проверьте ошибки';status.dataset.state='error';}
   else {status.textContent=header.dataset.planReadonly==='true'?'Режим просмотра':resourceState==='saved'?'Ресурс сохранён':allocation?'Сохранено':header.querySelector('[data-plan-action-title-locked]')?'План зафиксирован':status.textContent||'Сохранено';status.dataset.state='saved';}
 }
 function markPlanDirty(){if(allocation){allocation.dataset.dirty='true';warning.hidden=true;update();}}
 function isPlanInput(input){return input.form===allocation&&(allocation.id!=='allocation-editor'?Boolean(input.name):/^(works|resources)-\d+-/.test(input.name||'')||input.matches('[data-item-id]'));}
 ['input','change'].forEach(name=>allocation?.addEventListener(name,event=>{if(isPlanInput(event.target))markPlanDirty();}));
 document.addEventListener('click',event=>{if(event.target.closest('#allocation-editor [data-add],#allocation-editor [data-remove-row]'))markPlanDirty();});
 function canLeave(actionForm=null){
   if(window.gprResourceEditor?.hasPendingChanges()){
     window.gprResourceEditor.revealPending();warning.textContent='Сначала сохраните или отмените изменения ресурса.';warning.hidden=false;return false;
   }
   if(allocation?.dataset.dirty==='true'&&actionForm!==allocation){warning.textContent='Есть несохранённые изменения плана. Нажмите «Сохранить».';warning.hidden=false;return false;}
   return true;
 }
 function updateWorkflow(workflow){
   if(!workflow)return;
   header.querySelectorAll('[data-gpr-ready-form]').forEach(form=>form.hidden=workflow.resources_ready);
   header.querySelectorAll('[data-gpr-submit-form]').forEach(form=>{form.hidden=!workflow.resources_ready;form.querySelector('button').disabled=!workflow.estimator_approved;});
   const phase=header.querySelector('[data-stage=preparation]');
   if(phase){phase.dataset.state=workflow.handed?'current':'pending';const note=phase.querySelector('[data-stage-note]');if(note)note.hidden=!(workflow.resources_ready&&workflow.estimator_approved);
     [['resources','Ресурсы',workflow.resources_ready],['prices','Цены',workflow.estimator_approved]].forEach(([key,label,ready])=>{const branch=phase.querySelector('[data-branch='+key+']');if(branch){branch.dataset.state=ready?'done':workflow.handed?'current':'pending';branch.textContent=label+': '+(ready?'Завершено':workflow.handed?'Сейчас':'Предстоит');}});
   }
 }
 window.gprSaveUI={markPlanDirty,canLeave,updateWorkflow};
 document.addEventListener('gpr-save-state',event=>{resourceState=event.detail.state;warning.hidden=true;update();});
 allocation?.addEventListener('submit',event=>{
   if(!canLeave(allocation)){event.preventDefault();return;}
   if(!event.defaultPrevented){planSaving=true;update();}
 });
 document.addEventListener('click',event=>{
   const action=event.target.closest('a[href],[data-plan-action-header] button,[data-plan-more-menu] button');if(!action)return;
   const localAnchor=action.tagName==='A'&&action.getAttribute('href')?.startsWith('#');
   const newTab=action.tagName==='A'&&(action.target==='_blank'||event.ctrlKey||event.metaKey||event.shiftKey);
   if(!localAnchor&&!newTab&&(action.tagName==='A'||action.type==='submit')&&!canLeave(action.form)){event.preventDefault();return;}
   if(action.dataset.planOpen){const section=document.getElementById(action.dataset.planOpen);if(section)section.open=true;}
   if(action.closest('[data-plan-more-menu]')&&(action.tagName==='A'||action.type==='button'))action.closest('[data-plan-more-menu]').hidePopover?.();
 });
 document.addEventListener('submit',event=>{if(event.target.closest('[data-plan-action-header],[data-plan-more-menu]')){if(!canLeave(event.target)){event.preventDefault();return;}status.textContent='Выполнение действия…';status.dataset.state='saving';}});
 const handoff=document.querySelector('[data-gpr-handoff]');
 document.querySelectorAll('[data-open-handoff]').forEach(button=>button.addEventListener('click',()=>{if(canLeave())handoff?.showModal();}));
 handoff?.querySelectorAll('[data-close-handoff]').forEach(button=>button.addEventListener('click',()=>handoff.close()));
 handoff?.querySelector('form').addEventListener('submit',event=>{if(!canLeave()){event.preventDefault();return;}status.textContent='Передача плана…';status.dataset.state='saving';});
 const picker=document.querySelector('[data-plan-month-picker]');
 if(picker){
   const year=picker.querySelector('[data-plan-year]'),month=picker.querySelector('[data-plan-month]');
   const current=picker.dataset.currentMonth,options=Array.from(month.options).map(o=>({value:o.value,label:o.textContent}));
   function rebuild(value){month.replaceChildren();options.filter(o=>o.value.startsWith(year.value+'-')).forEach(o=>month.add(new Option(o.label,o.value)));if(Array.from(month.options).some(o=>o.value===value))month.value=value;}
   function navigate(){if(!canLeave()){year.value=current.slice(0,4);rebuild(current);return;}const url=new URL(location.href);url.searchParams.set('month',month.value);location.assign(url.href);}
   rebuild(current);year.addEventListener('change',()=>{rebuild('');navigate();});month.addEventListener('change',navigate);
 }
 update();
})();
