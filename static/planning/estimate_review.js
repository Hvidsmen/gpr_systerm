(() => {
 'use strict';
 const root=document.querySelector('.estimate-review');if(!root)return;
 const cards=[...root.querySelectorAll('.review-work')],groups=[...root.querySelectorAll('[data-review-group]')];
 const search=root.querySelector('#review-search'),status=root.querySelector('#review-status'),section=root.querySelector('#review-section'),kind=root.querySelector('#review-kind');
 const all=root.querySelector('#review-select-all'),boxes=cards.map(card=>card.querySelector('.review-select')).filter(Boolean);
 const prices=[...root.querySelectorAll('[data-review-price-input]')],batch=root.querySelector('#review-prices'),live=root.querySelector('[data-review-live]');
 const byId=new Map(cards.map(card=>[card.dataset.workId,card]));let busy=false,editable=Boolean(batch);
 const changed=()=>prices.filter(input=>input.value===''||Number(input.value)!==Number(input.dataset.original));
 function message(text,error=false){if(live){live.textContent=text;live.classList.toggle('is-error',error);}}
 function rowStatus(card){return card.querySelector('[data-review-price-input]')?.classList.contains('is-dirty')?'pending':card.dataset.status;}
 function counts(){
  const shown=cards.filter(card=>!card.hidden),selected=boxes.filter(box=>box.checked).length;
  root.querySelector('#review-visible').textContent='Показано: '+shown.length+' из '+cards.length;
  const selectedText=root.querySelector('#review-selected');if(selectedText)selectedText.textContent='Выбрано: '+selected;
  const bulk=root.querySelector('#review-bulk');if(bulk)bulk.hidden=!editable||selected===0;
  root.querySelector('#review-empty').hidden=shown.length!==0;
  const checked=cards.filter(card=>rowStatus(card)==='checked').length;
  const progress=root.querySelector('.review-progress');
  progress.querySelector('strong').textContent='Проверено цен '+checked+' из '+cards.length;
  progress.querySelector('progress').value=checked;progress.querySelector('progress').max=cards.length||1;
  progress.querySelector('.small').textContent=checked===cards.length?'Все цены проверены':'Осталось: '+(cards.length-checked);
  if(all){const visible=shown.map(card=>card.querySelector('.review-select')).filter(Boolean),n=visible.filter(box=>box.checked).length;all.checked=n>0&&n===visible.length;all.indeterminate=n>0&&n<visible.length;all.disabled=!editable||busy;}
  for(const group of groups){
   const members=cards.filter(card=>group.contains(card)),visible=members.filter(card=>!card.hidden);group.hidden=visible.length===0;
   const caption=group.querySelector(':scope > summary [data-review-group-count]');if(caption)caption.textContent='Проверено '+visible.filter(card=>rowStatus(card)==='checked').length+' из '+visible.length+(visible.length!==members.length?' · по фильтру':'');
   const select=group.querySelector(':scope > summary [data-review-group-select]');
   if(select){const children=visible.map(card=>card.querySelector('.review-select')).filter(Boolean),n=children.filter(box=>box.checked).length;select.checked=n>0&&n===children.length;select.indeterminate=n>0&&n<children.length;select.disabled=!editable||busy;}
  }
  for(const button of root.querySelectorAll('button[type=submit],form button[name=action]'))button.disabled=busy||!editable;
  for(const button of root.querySelectorAll('[data-review-finish],button[value=approve]'))button.disabled=busy||!editable||changed().length>0||checked<cards.length;
  const save=root.querySelector('[data-save-review-prices]');if(save)save.disabled=busy||!editable||changed().length===0;
 }
 function filter(){
  const query=search.value.trim().toLocaleLowerCase();
  for(const card of cards){
   card.hidden=!card.dataset.name.toLocaleLowerCase().includes(query)||(status.value&&rowStatus(card)!==status.value)||(section.value&&card.dataset.section!==section.value)||(kind.value&&card.dataset.kind!==kind.value);
   const box=card.querySelector('.review-select');if(box&&card.hidden)box.checked=false;
  }
  counts();
  if(query||status.value||section.value||kind.value)groups.filter(group=>!group.hidden).forEach(group=>group.open=true);
  if(query)cards.filter(card=>!card.hidden).forEach(card=>card.open=true);
 }
 function markPrices(){
  for(const input of prices){
   const dirty=input.value===''||Number(input.value)!==Number(input.dataset.original);input.classList.toggle('is-dirty',dirty);
   const card=input.closest('.review-work'),badge=card.querySelector('[data-review-row-status]');
   badge.textContent=dirty?'Не сохранено':card.dataset.status==='checked'?'Цена проверена':card.dataset.status==='rejected'?'Цена отклонена':'Нужна проверка';
  }
  counts();
 }
 function clearErrors(){root.querySelectorAll('[data-price-error],[data-batch-error],.review-action-error').forEach(node=>node.textContent='');prices.forEach(input=>{input.removeAttribute('aria-invalid');input.classList.remove('is-invalid');});}
 function showErrors(data,form){
  const rowFormError=form.querySelector('.review-action-error');if(rowFormError)rowFormError.textContent=data.message||'Не удалось выполнить действие.';
  for(const [key,errors] of Object.entries(data.errors||{})){
   const text=errors.map(error=>typeof error==='string'?error:error.message).join(' '),card=byId.get(key);
   if(card){const input=card.querySelector('[data-review-price-input]');input?.setAttribute('aria-invalid','true');input?.classList.add('is-invalid');card.querySelector('[data-price-error]').textContent=text;}
   else{const field=root.querySelector('[data-batch-error="'+key.replace(/[^a-z_]/g,'')+'"]');if(field)field.textContent=text;}
  }
  message(data.message||'Проверьте отмеченные поля. Изменения не сохранены.',true);
 }
 function updateState(state){
  editable=state.can_review;
  const taskLabel=root.querySelector('[data-review-task-status]');if(taskLabel)taskLabel.textContent=state.task_label;
  if(batch){batch.querySelector('[name=snapshot]').value=state.snapshot||'';batch.hidden=!editable;}
  root.querySelectorAll('[data-review-mutable]').forEach(node=>node.hidden=!editable);
  for(const row of state.rows){
   const card=byId.get(String(row.id));if(!card)continue;card.dataset.status=row.status;
   const input=card.querySelector('[data-review-price-input]');if(input){input.value=row.price;input.dataset.original=row.price;input.disabled=!editable;input.classList.remove('is-dirty');}
   const badge=card.querySelector('[data-review-row-status]');badge.className='review-work-status review-status-'+row.status;badge.textContent=row.status==='checked'?'Цена проверена':row.status==='rejected'?'Цена отклонена':'Нужна проверка';
   const comment=card.querySelector('[data-review-row-comment]');comment.textContent=row.comment||'';comment.hidden=row.status!=='rejected';
   const actions=card.querySelector('[data-review-action-form]');if(actions)actions.hidden=!editable;
   if(row.history){
    const history=card.querySelector('.review-price-history');history.querySelector('summary').textContent='История цен · '+row.history.length;
    const body=history.querySelector('tbody');body.replaceChildren();
    for(const price of row.history){const tr=document.createElement('tr');for(const value of [price.date,price.price,price.reason]){const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);}
   }
  }
  boxes.forEach(box=>box.disabled=!editable);filter();
 }
 [search,status,section,kind].forEach(field=>{field.addEventListener('input',filter);field.addEventListener('change',filter);});
 root.querySelector('#review-reset').addEventListener('click',()=>{[search,status,section,kind].forEach(field=>field.value='');filter();});
 boxes.forEach(box=>{box.addEventListener('click',event=>event.stopPropagation());box.addEventListener('change',counts);});
 all?.addEventListener('change',()=>{for(const card of cards.filter(card=>!card.hidden)){const box=card.querySelector('.review-select');if(box)box.checked=all.checked;}counts();});
 root.querySelectorAll('[data-review-group-select]').forEach(box=>{box.addEventListener('click',event=>event.stopPropagation());box.addEventListener('change',()=>{cards.filter(card=>!card.hidden&&box.closest('[data-review-group]').contains(card)).forEach(card=>{const child=card.querySelector('.review-select');if(child)child.checked=box.checked;});counts();});});
 root.querySelectorAll('[data-review-expand]').forEach(button=>button.addEventListener('click',()=>{const level=button.dataset.reviewExpand;root.querySelectorAll('[data-review-level]').forEach(node=>node.open=level==='all'||level==='objects'&&node.dataset.reviewLevel==='section'||level==='packages'&&['section','object'].includes(node.dataset.reviewLevel));}));
 prices.forEach(input=>{input.addEventListener('click',event=>event.stopPropagation());input.closest('label').addEventListener('click',event=>event.stopPropagation());input.addEventListener('input',()=>{clearErrors();markPrices();message(changed().length?'Есть изменения цен: '+changed().length+'. Сохраните их одной кнопкой.':'Все цены сохранены.');});});
 root.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('#review-prices,#review-bulk,#review-final,[data-review-action-form]'))return;
  event.preventDefault();if(busy||!editable)return;clearErrors();
  const edits=changed(),isPrices=form===batch;
  if(!isPrices&&edits.length){message('Сначала сохраните изменённые цены.',true);root.querySelector('[data-save-review-prices]')?.focus({preventScroll:true});return;}
  const data=new FormData(form);
  if(isPrices){
   if(!edits.length){message('Изменений цен нет.');return;}
   const invalid=edits.find(input=>!input.value||!input.checkValidity());
   if(invalid){invalid.setAttribute('aria-invalid','true');invalid.closest('.review-work').querySelector('[data-price-error]').textContent='Введите неотрицательную цену с точностью до копеек.';invalid.focus();return;}
   const date=form.querySelector('[name=effective_from]'),reason=form.querySelector('[name=reason]');
   if(!date.value||!reason.value.trim()){showErrors({message:'Укажите дату действия и основание для изменённых цен.',errors:{...(!date.value?{effective_from:[{message:'Укажите дату действия.'}]}:{}),...(!reason.value.trim()?{reason:[{message:'Укажите основание.'}]}:{})}},form);(!date.value?date:reason).focus();return;}
   data.set('action','save_prices');data.set('changes',JSON.stringify(edits.map(input=>({work_id:Number(input.dataset.workId),price:input.value}))));
  }else data.set('action',event.submitter?.value||'approve');
  busy=true;prices.forEach(input=>input.disabled=true);counts();message(isPrices?'Сохранение цен…':'Проверка цены…');
  try{
   const response=await fetch(location.pathname,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'}});
   if(response.redirected)throw Error('Сессия завершена. Сохраните введённые цены у себя и войдите в портал заново.');
   const result=await response.json().catch(()=>{throw Error(response.status===403?'Действие недоступно. Проверьте назначение и права.':'Сервер не подтвердил действие. Повторите попытку.');});
   if(!response.ok||!result.ok){showErrors(result,form);return;}
   updateState(result.review);message(result.message||'Сохранено.');
  }catch(error){message(error.message||'Ошибка соединения. Введённые цены остаются на экране.',true);}
  finally{busy=false;prices.forEach(input=>input.disabled=!editable);counts();}
 });
 window.addEventListener('beforeunload',event=>{if(changed().length||busy){event.preventDefault();event.returnValue='';}});
 filter();markPrices();
})();
