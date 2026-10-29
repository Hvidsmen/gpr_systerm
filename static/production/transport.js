(() => {
  'use strict';
  const root=document.querySelector('[data-transport-board]');if(!root)return;
  const calendar=root.querySelector('.transport-calendar'),currentDay=root.querySelector(`[data-calendar-day="${root.dataset.date}"]`);
  if(calendar&&currentDay)calendar.scrollLeft=Math.max(0,currentDay.offsetLeft-calendar.clientWidth/2);
  const dialog=root.querySelector('[data-action-dialog]'),form=root.querySelector('[data-transport-form]');
  if(!dialog||!form)return;
  const token=form.querySelector('[name=csrfmiddlewaretoken]').value;
  const sessionField=form.elements.session,session=sessionField.value||([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));sessionField.value=session;
  const field=name=>form.elements[name], wrapper=name=>form.querySelector(`[data-transport-field="${name}"]`);
  const rows=[...root.querySelectorAll('[data-transport-row]')],checks=[...root.querySelectorAll('[data-select-machine]')];
  let reserved=[],submitting=false,pending=false;
  const arrivalIds=JSON.parse(document.getElementById('transport-arrival-ids').textContent).map(String);
  const titles={ARRIVAL:'Оформить приезд',DAY:'Отметить работу за день',CONDITION:'Изменить техническое состояние',TRANSFER:'Перевести в другой ВЖГ',DEPARTURE:'Оформить выезд',WRITEOFF:'Списать технику'};
  const selected=()=>checks.filter(c=>c.checked).map(c=>c.value);
  const error=(message,inside=false)=>{const box=root.querySelector(inside?'[data-dialog-error]':'[data-transport-error]');box.textContent=message;box.hidden=!message;};
  function counts(){const ids=selected();root.querySelector('[data-selection-count]').textContent=`Выбрано: ${ids.length}`;root.querySelectorAll('[data-needs-selection]').forEach(b=>b.disabled=!ids.length);}
  checks.forEach(c=>c.addEventListener('change',counts));root.querySelector('[data-select-all]')?.addEventListener('change',e=>{checks.forEach(c=>{if(c.getClientRects().length&&!c.closest('details:not([open])'))c.checked=e.target.checked;});counts();});if(checks.length||root.querySelector('[data-selection-count]'))counts();
  async function claim(action,ids){
    if(!ids.length)return {};
    const data=new URLSearchParams({camp:root.dataset.camp,date:root.dataset.date,session,action});ids.forEach(id=>data.append('equipment',id));
    const response=await fetch(root.dataset.claimUrl,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':token},body:data});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось проверить редактирование техники.');return result.owners;
  }
  window.transportBoardLocks={session,claim};
  function showFields(){
    const action=field('action').value,day=action==='DAY',arrival=action==='ARRIVAL';
    form.querySelector('[data-dialog-title]').textContent=titles[action]||'Учёт техники';
    wrapper('equipment').hidden=false;wrapper('condition').hidden=!(arrival||action==='CONDITION');wrapper('target_camp').hidden=action!=='TRANSFER';
    wrapper('status').hidden=!day;wrapper('target').hidden=!day||field('status').value!=='WORKED'||field('repeat').checked;wrapper('repeat').hidden=!day||field('status').value!=='WORKED';
    form.querySelector('[data-arrival-help]').hidden=!arrival;form.querySelector('[data-writeoff-help]').hidden=action!=='WRITEOFF';
    field('target_camp').required=action==='TRANSFER';field('status').required=day;field('condition').required=arrival||action==='CONDITION';
  }
  async function reserve(ids){
    pending=true;form.querySelector('[type=submit]').disabled=true;
    try{const added=ids.filter(id=>!reserved.includes(id)),removed=reserved.filter(id=>!ids.includes(id));if(added.length)await claim('acquire',added);if(removed.length)await claim('release',removed);reserved=ids;error('',true);}
    catch(e){error(e.message,true);throw e;}
    finally{pending=false;form.querySelector('[type=submit]').disabled=false;}
  }
  async function open(action,ids=[]){
    error('');error('',true);field('action').value=action;field('repeat').checked=false;field('target').value='';field('comment').value='';field('condition').value='READY';field('status').value='WORKED';
    for(const o of field('equipment').options){o.disabled=action==='ARRIVAL'?!arrivalIds.includes(o.value):!rows.some(r=>r.dataset.transportRow===o.value);o.hidden=o.disabled;o.selected=ids.includes(o.value);}
    form.querySelector('[data-machine-search]').value='';
    if(ids.length===1&&action==='DAY'){
      const row=rows.find(r=>r.dataset.transportRow===ids[0]);field('status').value=row.dataset.status||'WORKED';field('target').value=row.dataset.target;field('comment').value=row.dataset.comment||'';
    }
    try{if(ids.length)await reserve(ids);showFields();dialog.showModal();}catch(e){error(e.message);}
  }
  root.querySelectorAll('[data-transport-action]').forEach(b=>b.addEventListener('click',()=>open(b.dataset.transportAction,b.dataset.transportAction==='ARRIVAL'?[]:selected())));
  root.querySelectorAll('[data-edit-machine]').forEach(b=>b.addEventListener('click',()=>{const row=rows.find(r=>r.dataset.transportRow===b.dataset.editMachine);open(root.dataset.process==='state'?'CONDITION':'DAY',[b.dataset.editMachine]);}));
  root.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>dialog.close()));
  dialog.addEventListener('close',()=>{if(!submitting){claim('release',reserved).catch(()=>{});reserved=[];}});
  field('equipment').addEventListener('change',()=>reserve([...field('equipment').selectedOptions].map(o=>o.value)).catch(()=>{}));
  field('status').addEventListener('change',showFields);field('repeat').addEventListener('change',showFields);
  form.querySelector('[data-machine-search]').addEventListener('input',e=>{const terms=e.target.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);for(const option of field('equipment').options)option.hidden=option.disabled||(!option.selected&&!terms.every(t=>option.textContent.toLocaleLowerCase().includes(t)));});
  form.addEventListener('submit',async e=>{if(submitting)return;e.preventDefault();if(pending)return;const ids=[...field('equipment').selectedOptions].map(o=>o.value);try{await reserve(ids);submitting=true;form.querySelector('[type=submit]').disabled=true;HTMLFormElement.prototype.submit.call(form);}catch(_){}});
  async function poll(){
    try{if(reserved.length&&dialog.open)await claim('renew',reserved);const ids=rows.map(r=>r.dataset.transportRow),owners={};for(let i=0;i<ids.length;i+=100)Object.assign(owners,await claim('status',ids.slice(i,i+100)));root.querySelectorAll('[data-lock-owner]').forEach(el=>{const lock=owners[el.dataset.lockOwner];el.textContent=lock?(lock.mine?'Вы редактируете':`Редактирует: ${lock.owner}`):'';});}catch(e){if(dialog.open)error(e.message,true);}
  }
  setInterval(poll,15000);poll();
  window.addEventListener('pagehide',()=>{if(reserved.length&&!submitting){const data=new URLSearchParams({camp:root.dataset.camp,date:root.dataset.date,session,action:'release',csrfmiddlewaretoken:token});reserved.forEach(id=>data.append('equipment',id));navigator.sendBeacon(root.dataset.claimUrl,data);}});
  if(dialog.dataset.open==='1'){showFields();dialog.showModal();}
})();
