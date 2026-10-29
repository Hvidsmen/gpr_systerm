(()=>{
 'use strict';const root=document.querySelector('[data-transport-board]');if(!root)return;
 root.querySelectorAll('[data-repair-dialog]').forEach(dialog=>{
  const row=dialog.closest('[data-transport-row]'),form=dialog.querySelector('[data-repair-form]');
  async function close(){
   if(document.activeElement&&dialog.contains(document.activeElement))document.activeElement.blur();
   await Promise.resolve();await row.queue;
   try{await row.releaseTransport?.();}catch{/* A closed editor releases its lease by expiry if the connection is lost. */}
   dialog.close();
  }
  row.querySelector('[data-open-repair]').addEventListener('click',()=>dialog.showModal());
  root.addEventListener('transport:row-saved',event=>{
   if(root.dataset.process==='state'&&event.detail.row===row&&event.detail.field==='condition'&&event.detail.condition==='REPAIR'&&!dialog.open){
    dialog.showModal();
   }
  });
  dialog.querySelectorAll('[data-close-repair]').forEach(button=>button.addEventListener('click',close));
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  form.addEventListener('submit',event=>{event.preventDefault();close();});
 });
})();
