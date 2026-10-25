(() => {
 const header=document.querySelector('[data-plan-action-header]');if(!header)return;
 header.querySelectorAll('[data-plan-open]').forEach(link=>link.addEventListener('click',()=>{const section=document.getElementById(link.dataset.planOpen);if(section)section.open=true;}));
 const allocation=document.getElementById('allocation-editor')||document.getElementById('object-work-editor');
 const status=document.createElement('p');status.className='small text-danger mb-0 mt-1';status.role='status';status.hidden=true;header.append(status);
 allocation?.addEventListener('input',event=>{if(event.target.form===allocation&&!event.target.matches('[data-fold-level]'))allocation.dataset.dirty='true';});
 allocation?.addEventListener('change',event=>{if(event.target.form===allocation&&!event.target.matches('[data-fold-level]'))allocation.dataset.dirty='true';});
 header.addEventListener('click',event=>{
   const action=event.target.closest('a,button');if(!action)return;
   const navigates=action.tagName==='A'||action.type==='submit';if(!navigates)return;
   if(window.gprResourceEditor?.hasPendingChanges()){
     event.preventDefault();window.gprResourceEditor.revealPending();status.textContent='Сначала сохраните или отмените изменения ресурса.';status.hidden=false;return;
   }
   if(allocation?.dataset.dirty==='true'&&action.form!==allocation){event.preventDefault();status.textContent='Есть несохранённые изменения. Нажмите «Сохранить».';status.hidden=false;}
 });
})();
