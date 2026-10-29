(()=>{
 'use strict';const form=document.querySelector('[data-production-form]'),board=document.querySelector('[data-transport-board]'),storageKey='production-copy-previous-result';let busy=false;
 function show(root,kind,result,error){
  const host=root.querySelector('[data-copy-result="'+kind+'"]');if(!host)return;host.replaceChildren();host.classList.toggle('text-danger',Boolean(error));
  if(error){host.textContent=error;return;}
  const source=new Date(result.source_date+'T12:00:00').toLocaleDateString('ru-RU');
  host.append(document.createTextNode(result.added||result.skipped?`Из ${source}: скопировано ${result.added}, пропущено ${result.skipped}.`:`За ${source} нет записей для переноса.`));
  if(result.warnings?.length){const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Пояснения · '+result.warnings.length;details.append(summary);const list=document.createElement('ul');for(const message of result.warnings){const li=document.createElement('li');li.textContent=message;list.append(li);}details.append(list);host.append(details);}
 }
 try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');sessionStorage.removeItem(storageKey);if(saved&&saved.url===location.href&&Date.now()-saved.at<60000&&board)show(board,'equipment',saved.result);}catch{}
 document.querySelectorAll('[data-copy-previous]').forEach(button=>button.addEventListener('click',async()=>{
  if(busy)return;busy=true;const kind=button.dataset.copyPrevious,root=kind==='equipment'?board:form,host=root.querySelector('[data-copy-result="'+kind+'"]');
  const buttons=[...document.querySelectorAll('[data-copy-previous]')],disabled=buttons.map(b=>b.disabled);buttons.forEach(b=>b.disabled=true);host.textContent='Копирование…';host.classList.remove('text-danger');
  let controls=[];
  try{
   let data,csrf;
   if(kind==='equipment'){
    await Promise.all([...board.querySelectorAll('[data-transport-row]')].map(row=>row.queue));
    if(board.querySelector('.transport-cell-failed'))throw new Error('Сначала исправьте несохранённые поля техники.');
    controls=[...board.querySelectorAll('input,select,textarea,button')].map(el=>({el,disabled:el.disabled}));controls.forEach(item=>item.el.disabled=true);
    csrf=board.querySelector('[name=csrfmiddlewaretoken]').value;const locks=window.transportBoardLocks;
    data={camp:board.dataset.camp,date:board.dataset.date,signed:board.dataset.inlineSigned,session:locks.session};
   }else{
    if(form.dataset.resetRequired==='1')throw new Error('Производственная часть сброшена. Обновите форму.');
    if(kind==='links'&&!await window.productionAutoSave.flushPeople())throw new Error('Сначала исправьте несохранённые поля сотрудников.');
    const field=(row,name)=>row.querySelector('[name$="-'+name+'"]'),people=[...form.querySelectorAll('[data-production-person]')];
    csrf=form.productionSession.csrf;data={...form.productionSession.scope,kind,replace_legacy:Boolean(form.elements.namedItem('replace_legacy')?.checked),keep:people.map(form.productionKeyOf).filter(Boolean),revisions:Object.fromEntries(people.filter(row=>field(row,'id').value).map(row=>[field(row,'id').value,Number(row.dataset.linksRevision||0)]))};
   }
   const response=await fetch(root.dataset.copyPreviousUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(data)});if(!(response.headers.get('content-type')||'').includes('application/json'))throw new Error('Не удалось скопировать данные. Обновите форму и проверьте доступ.');const result=await response.json();
   if(!response.ok)throw new Error(result.error||'Не удалось скопировать данные.');
   show(root,kind,result);
   if(kind==='equipment'&&result.added){try{sessionStorage.setItem(storageKey,JSON.stringify({url:location.href,at:Date.now(),result}));}catch{}busy=false;location.reload();}
   else if(form){if(result.report)form.dispatchEvent(new CustomEvent('production-report-update',{detail:result.report}));await form.productionRefresh();}
  }catch(error){show(root,kind,null,error.message);}
  finally{controls.forEach(({el,disabled})=>el.disabled=disabled);buttons.forEach((b,i)=>b.disabled=disabled[i]);busy=false;}
 }));
 window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
})();
