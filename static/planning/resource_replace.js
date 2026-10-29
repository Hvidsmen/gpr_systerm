(() => {
 'use strict';
 const dialog=document.querySelector('[data-resource-replace-dialog]');if(!dialog)return;
 const body=dialog.querySelector('[data-resource-replace-body]');let url='',busy=false;
 function notice(text){body.replaceChildren();const title=document.createElement('h2');title.id='resource-replace-title';title.className='h5';title.textContent='Замена ресурса';const message=document.createElement('p');message.textContent=text;const close=document.createElement('button');close.type='button';close.className='btn btn-outline-secondary';close.dataset.replaceClose='';close.textContent='Закрыть';body.append(title,message,close);}
 async function load(response){if(response.redirected){notice('Войдите в портал заново, затем откройте замену.');return;}const content=await response.text();if(response.status===403||response.status===404){notice('Ресурс недоступен для замены. Проверьте права и состояние плана.');return;}if(!response.ok&&response.status!==400){notice('Не удалось открыть замену. Повторите попытку.');return;}body.innerHTML=content;}
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-resource-replace-url]');if(!button)return;
  event.preventDefault();event.stopPropagation();if(busy)return;
  if(window.smgSaveUI?.canLeave&&!window.smgSaveUI.canLeave())return;
  if(window.planWorkbench?.canLeave&&!window.planWorkbench.canLeave())return;
  const native=document.getElementById('allocation-editor');
  if(native?.dataset.dirty==='true'||document.querySelector('[data-workspace-form][data-dirty=true]')){notice('Сначала сохраните изменения плана, затем откройте замену.');dialog.showModal();return;}
  url=button.dataset.resourceReplaceUrl;notice('Загрузка…');dialog.showModal();busy=true;
  try{await load(await fetch(url,{headers:{'X-Requested-With':'XMLHttpRequest'}}));}catch{notice('Не удалось загрузить форму. Проверьте соединение и повторите попытку.');}finally{busy=false;}
 });
 dialog.addEventListener('click',event=>{if(event.target.closest('[data-replace-close]')&&!busy)dialog.close();});
 dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 dialog.addEventListener('change',event=>{
  if(!event.target.matches('[data-replacement-target],[name=add_crew]'))return;
  body.querySelector('[data-replace-apply]')?.remove();body.querySelector('.replacement-preview')?.remove();
 });
 dialog.addEventListener('submit',async event=>{
  const form=event.target.closest('[data-resource-replace-form]');if(!form)return;event.preventDefault();if(busy)return;
  const data=new FormData(form);data.set('action',event.submitter?.value||'preview');busy=true;
  const buttons=[...form.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try{
   const response=await fetch(url,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'}});
   if(response.headers.get('content-type')?.includes('application/json')){
    const result=await response.json();if(!response.ok||!result.saved)throw Error('Сервер не подтвердил замену.');
    window.location.reload();return;
   }
   await load(response);
  }catch(error){const hint=body.querySelector('[data-replace-error]');if(hint)hint.textContent=error.message||'Не удалось сохранить замену. Повторите попытку.';else notice('Не удалось выполнить замену. Повторите попытку.');}
  finally{busy=false;buttons.forEach(button=>button.disabled=false);}
 });
})();
