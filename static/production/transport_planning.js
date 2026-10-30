(()=>{
 'use strict';const root=document.querySelector('[data-transport-board]');if(!root||root.dataset.process!=='day'||root.dataset.dayView!=='plan')return;
 const key='transport-plan-position',locks=window.transportBoardLocks,csrf=root.querySelector('[name=csrfmiddlewaretoken]')?.value;let pending=false;
 const groupKey=el=>(el.closest('[data-outside-facts]')?'outside:':'plan:')+el.dataset.transportGroup;
 function remember(){try{sessionStorage.setItem(key,JSON.stringify({url:location.href,y:scrollY,x:root.querySelector('[data-transport-table-scroll]').scrollLeft,at:Date.now(),outside:Boolean(root.querySelector('[data-outside-facts]')?.open||root.querySelector('[data-outside-dialog]')?.open),outsideType:root.querySelector('[data-outside-dialog][open] [data-type-picker]')?.dataset.planningType,closed:[...root.querySelectorAll('[data-transport-group]:not([open])')].map(groupKey)}));}catch{}}
 function reload(){remember();location.reload();}
 try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');sessionStorage.removeItem(key);if(saved&&saved.url===location.href&&Date.now()-saved.at<60000)requestAnimationFrame(()=>{root.querySelectorAll('[data-transport-group]').forEach(el=>el.open=!saved.closed.includes(groupKey(el)));const outside=root.querySelector('[data-outside-facts]');if(outside)outside.open=Boolean(saved.outside);root.querySelector('[data-transport-table-scroll]').scrollLeft=saved.x;scrollTo(0,saved.y);if(saved.outsideType)root.querySelector('[data-transport-type="'+saved.outsideType+'"]')?.scrollIntoView({block:'center'});});}catch{}
 const pool=JSON.parse(document.getElementById('transport-machine-pool')?.textContent||'[]');
 const labels=['Нужный тип · этот ВЖГ','Нужный тип · другие ВЖГ','Нужный тип · без оформленного ВЖГ','Остальная техника · этот ВЖГ','Остальная техника · другие ВЖГ','Остальная техника · без оформленного ВЖГ'];
 function fillMachines(form,typeId){
  const select=form.elements.equipment;select.replaceChildren(new Option('Выберите фактически работавшую машину',''));
  const groups=Array.from({length:6},()=>[]);
  for(const item of pool)groups[(String(item.type)===String(typeId)?0:3)+item.location].push(item);
  groups.forEach((items,index)=>{if(!items.length)return;const group=document.createElement('optgroup');group.label=labels[index];for(const item of items)group.append(new Option(item.label,String(item.id)));select.append(group);});
  form.querySelector('[data-choice-location]').textContent='';
 }
 root.addEventListener('change',event=>{const select=event.target;if(select.name!=='equipment'||!select.closest('[data-type-picker]'))return;const form=select.closest('form'),item=pool.find(item=>String(item.id)===select.value);form.querySelector('[data-choice-location]').textContent=item?item.whereabouts+' · '+(String(item.type)===String(form.dataset.planningType||form.closest('[data-transport-type]')?.dataset.transportType)?'Соответствует плановому типу':'Фактический тип: '+item.type_name):'';});
 const outsideDialog=root.querySelector('[data-outside-dialog]');
 if(outsideDialog){
  const form=outsideDialog.querySelector('form'),type=outsideDialog.querySelector('[data-outside-type]');
  root.querySelector('[data-add-outside]').addEventListener('click',()=>outsideDialog.showModal());
  outsideDialog.querySelector('[data-close-outside]').addEventListener('click',()=>{if(!pending)outsideDialog.close();});outsideDialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
  type.addEventListener('change',()=>{form.dataset.planningType=type.value;fillMachines(form,type.value);});
 }
 root.querySelectorAll('[data-type-add]').forEach(button=>button.addEventListener('click',()=>{const form=button.parentElement.querySelector('[data-type-picker]');form.hidden=!form.hidden;if(!form.hidden)fillMachines(form,form.closest('[data-transport-type]').dataset.transportType);button.setAttribute('aria-expanded',String(!form.hidden));if(!form.hidden)(form.querySelector('.search-select-button')||form.elements.equipment).focus();}));
 root.querySelectorAll('[data-type-picker]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();if(pending||!form.reportValidity())return;
  const equipment=form.elements.equipment.value,typeId=form.dataset.planningType||form.closest('[data-transport-type]')?.dataset.transportType,status=form.querySelector('[data-type-save]'),button=form.querySelector('[type=submit]');
  let saved=false;pending=true;button.disabled=true;status.classList.remove('text-danger');status.textContent='Сохранение…';
  try{
   await Promise.all([...root.querySelectorAll('[data-transport-row]')].map(row=>row.queue));
   if(root.querySelector('.transport-cell-failed'))throw new Error('Проверьте несохранённые поля машин перед добавлением.');
   await locks.claim('acquire',[equipment]);
   const response=await fetch(root.dataset.inlineUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({camp:root.dataset.camp,date:root.dataset.date,equipment,field:'assign',value:typeId,signed:root.dataset.inlineSigned,session:locks.session})});
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
