(()=>{
 'use strict';const root=document.querySelector('[data-transport-board]');if(!root||root.dataset.process!=='day'||root.dataset.dayView!=='plan')return;
 const key='transport-plan-position',locks=window.transportBoardLocks,csrf=root.querySelector('[name=csrfmiddlewaretoken]')?.value;let pending=false;
 function remember(){try{sessionStorage.setItem(key,JSON.stringify({url:location.href,y:scrollY,x:root.querySelector('[data-transport-table-scroll]').scrollLeft,at:Date.now(),closed:[...root.querySelectorAll('[data-transport-group]:not([open])')].map(el=>el.dataset.transportGroup)}));}catch{}}
 function reload(){remember();location.reload();}
 try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');sessionStorage.removeItem(key);if(saved&&saved.url===location.href&&Date.now()-saved.at<60000)requestAnimationFrame(()=>{root.querySelectorAll('[data-transport-group]').forEach(el=>el.open=!saved.closed.includes(el.dataset.transportGroup));root.querySelector('[data-transport-table-scroll]').scrollLeft=saved.x;scrollTo(0,saved.y);});}catch{}
 root.querySelectorAll('[data-type-add]').forEach(button=>button.addEventListener('click',()=>{const form=button.parentElement.querySelector('[data-type-picker]');form.hidden=!form.hidden;button.setAttribute('aria-expanded',String(!form.hidden));if(!form.hidden)(form.querySelector('.search-select-button')||form.elements.equipment).focus();}));
 root.querySelectorAll('[data-type-picker]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();if(pending||!form.reportValidity())return;
  const equipment=form.elements.equipment.value,type=form.closest('[data-transport-type]'),status=form.querySelector('[data-type-save]'),button=form.querySelector('[type=submit]');
  let saved=false;pending=true;button.disabled=true;status.classList.remove('text-danger');status.textContent='Сохранение…';
  try{
   await Promise.all([...root.querySelectorAll('[data-transport-row]')].map(row=>row.queue));
   if(root.querySelector('.transport-cell-failed'))throw new Error('Проверьте несохранённые поля машин перед добавлением.');
   await locks.claim('acquire',[equipment]);
   const response=await fetch(root.dataset.inlineUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({camp:root.dataset.camp,date:root.dataset.date,equipment,field:'assign',value:type.dataset.transportType,signed:root.dataset.inlineSigned,session:locks.session})});
   const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось добавить машину.');
   saved=true;
  }catch(error){status.classList.add('text-danger');status.textContent=error.message;}
  finally{if(!saved)await locks?.claim('release',[equipment]).catch(()=>{});pending=false;button.disabled=false;}
  if(saved)reload();
 }));
 root.querySelectorAll('[data-type-remove]').forEach(button=>button.addEventListener('click',()=>{const row=button.closest('[data-transport-row]'),status=row.querySelector('[data-detail-field=status]');button.disabled=true;status.value='CLEAR';status.dispatchEvent(new Event('change',{bubbles:true}));row.queue.finally(()=>button.disabled=false);}));
 root.addEventListener('transport:row-saved',event=>{
  const {row,field,status}=event.detail;if(field!=='status')return;
  const type=row.closest('[data-transport-type]'),rows=[...type.querySelectorAll('[data-transport-row]')],worked=rows.filter(row=>row.dataset.status==='WORKED').length;
  type.querySelector('[data-type-worked]').textContent=worked;type.querySelector('[data-type-idle]').textContent=rows.filter(row=>row.dataset.status==='IDLE').length;
  const remaining=type.querySelector('[data-type-remaining]');if(remaining)remaining.textContent=Math.max(0,Number(type.dataset.plan)-worked);
  if(!status)setTimeout(reload,0);
 });
 root.dataset.planningReady='1';
 window.addEventListener('beforeunload',event=>{if(pending){event.preventDefault();event.returnValue='';}});
})();
