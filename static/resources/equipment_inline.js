(()=>{
 const owners=new Map();
 document.addEventListener('click',event=>{
  const add=event.target.closest('[data-equipment-inline]');if(add){const dialog=document.querySelector(`[data-equipment-dialog="${add.dataset.equipmentInline}"]`);owners.set(dialog,document.getElementById(add.dataset.target));dialog.querySelector('[data-equipment-inline-errors]').textContent='';dialog.showModal();dialog.querySelector('input[name=name]').focus();}
  event.target.closest('[data-equipment-inline-close]')?.closest('dialog').close();
 });
 document.querySelectorAll('[data-equipment-inline-form]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();const dialog=form.closest('dialog'),target=owners.get(dialog),button=event.submitter,error=form.querySelector('[data-equipment-inline-errors]');button.disabled=true;error.textContent='';
  try{const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});if(response.redirected||!response.headers.get('content-type')?.includes('json'))throw new Error('Сессия истекла. Сохраните введённые данные и войдите снова.');const result=await response.json();if(!response.ok)throw new Error(Object.values(result.errors||{}).flat().join(' ')||'Не удалось добавить запись.');
   const selects=form.dataset.equipmentInlineForm==='group'?document.querySelectorAll('[data-equipment-group-select]'):[target];
   for(const select of selects){if(![...select.options].some(o=>o.value===result.value))select.add(new Option(result.label,result.value));}
   target.value=result.value;target.dispatchEvent(new Event('change',{bubbles:true}));dialog.close();form.reset();
  }catch(failure){error.textContent=failure.message;}finally{button.disabled=false;}
 }));
})();
