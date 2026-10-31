/* Per-work actuation uses ordinary row claims, signed baselines and autosave. */
(()=>{
 'use strict';
 const form=document.querySelector('[data-production-form]'),dialog=document.querySelector('[data-work-actuation-dialog]');
 if(!form||!dialog)return;
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),rows=()=>[...form.querySelectorAll('[data-work-fact-row]')];
 const list=dialog.querySelector('[data-actuation-list]'),search=dialog.querySelector('[data-actuation-search]'),type=dialog.querySelector('[data-actuation-type]'),reason=dialog.querySelector('[data-actuation-reason]'),apply=dialog.querySelector('[data-actuation-apply]'),error=dialog.querySelector('[data-actuation-error]');
 let items=[],busy=false;
 function count(){const total=items.filter(i=>i.checkbox.checked).length;dialog.querySelector('[data-actuation-count]').textContent='Выбрано: '+total;apply.disabled=busy||!total;}
 function filter(){const q=search.value.trim().toLocaleLowerCase('ru');items.forEach(i=>i.label.hidden=!i.text.toLocaleLowerCase('ru').includes(q));}
 function renderRow(row){const current=field(row,'actuation_type')?.value,label=row.querySelector('[data-work-actuation-label]');if(label)label.textContent=current?'· '+({DAY:'Дневная',NIGHT:'Ночная',FULL_DAY:'Суточная'}[current]||current):'';if(current)row.querySelector('[data-work-actuation]').open=true;}
 // Capture before claim/autosave listeners build the changed row snapshot.
 form.addEventListener('change',event=>{const row=event.target.closest('[data-work-fact-row]');if(!row)return;if(event.target===field(row,'actuation_type')){if(event.target.value)field(row,'actual_quantity').value='0';else field(row,'actuation_reason').value='';renderRow(row);}},true);
 form.addEventListener('input',event=>{const row=event.target.closest('[data-work-fact-row]');if(row&&event.target===field(row,'actual_quantity')&&field(row,'actuation_type')?.value)event.target.value='0';},true);
 rows().forEach(renderRow);form.addEventListener('production-live-applied',()=>rows().forEach(renderRow));
 form.querySelector('[data-work-actuation-open]').addEventListener('click',()=>{
  list.replaceChildren();items=[];search.value='';reason.value='';type.value='FULL_DAY';error.textContent='';type.dispatchEvent(new Event('change'));
  const seen=new Set();for(const row of rows()){
   const key=form.productionKeyOf(row);if(!key||seen.has(key)||field(row,'DELETE')?.checked)continue;seen.add(key);
   const label=document.createElement('label'),checkbox=document.createElement('input'),text=document.createElement('span');checkbox.type='checkbox';
   const title=row.querySelector('[data-work-title]')?.textContent||key,path=row.querySelector('[data-work-path]')?.textContent||'';text.textContent=title+' · '+path;
   label.dataset.workToken=key;label.append(checkbox,text);list.append(label);items.push({row,label,checkbox,text:text.textContent});checkbox.addEventListener('change',count);
  }count();dialog.showModal();search.focus();
 });
 reason.addEventListener('input',()=>{error.textContent='';});
 search.addEventListener('input',filter);dialog.querySelector('[data-actuation-select]').addEventListener('click',()=>{items.filter(i=>!i.label.hidden).forEach(i=>i.checkbox.checked=true);count();});
 dialog.querySelector('[data-actuation-clear]').addEventListener('click',()=>{items.forEach(i=>i.checkbox.checked=false);count();});
 dialog.querySelectorAll('[data-actuation-close]').forEach(button=>button.addEventListener('click',()=>{if(!busy)dialog.close();}));dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 type.addEventListener('change',()=>{reason.disabled=!type.value;dialog.querySelector('[data-actuation-warning]').textContent=type.value?'У выбранных работ факт станет нулевым. Остальные работы не изменятся.':'Актировка будет снята только у выбранных работ. Объёмы останутся нулевыми — внесите их заново при необходимости.';});
 apply.addEventListener('click',async()=>{
  error.textContent='';const selected=items.filter(i=>i.checkbox.checked).map(i=>i.row),kind=type.value,explanation=reason.value.trim();if(!selected.length)return;
  if(kind&&!explanation){error.textContent='Укажите причину актировки выбранных работ.';reason.focus();return;}
  busy=true;count();const acquired=[];
  try{
   if(!await window.productionAutoSave.flush())throw new Error('Сначала исправьте ошибки сохранения на вкладке «Работы».');
   form.productionApplying=true;
   for(const row of selected){if(!row.isConnected||field(row,'DELETE')?.checked)throw new Error('Список работ изменился. Закройте и откройте выбор заново.');const owned=field(row,'work_editing').value==='1';await form.productionClaimRow(row);if(!owned)acquired.push(row);}
   for(const row of selected){field(row,'actuation_type').value=kind;field(row,'actuation_reason').value=explanation;if(kind)field(row,'actual_quantity').value='0';renderRow(row);}
   form.productionApplying=false;
   selected.forEach(row=>field(row,'actuation_type').dispatchEvent(new Event('change',{bubbles:true})));
   if(!await window.productionAutoSave.flush())throw new Error('Изменения остались в форме. Проверьте ошибки сохранения; повторно применять актировку не нужно.');
   dialog.close();
  }catch(failure){error.textContent=failure.message;for(const row of acquired){if(!row.dataset.autoPending){delete row.dataset.entryDirty;try{await form.productionReleaseRow(row);}catch{}}}}
  finally{form.productionApplying=false;busy=false;count();}
 });
})();
