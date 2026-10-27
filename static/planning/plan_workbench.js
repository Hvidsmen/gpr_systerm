/* Shared editing surface. GPR binds to its existing formset; SMG uses daily cells. */
(() => {
 'use strict';
 const root=document.querySelector('[data-plan-workbench]');if(!root)return;
 const daily=root.dataset.planWorkbench==='smg',matrix=root.querySelector('[data-plan-matrix]');
 const native=document.getElementById('allocation-editor');
 const search=document.getElementById('smg-search'),kind=document.getElementById('smg-kind'),deviations=document.getElementById('smg-deviations');
 const notice=root.querySelector('[data-plan-copy-status]');
 const filters={};
 const message=text=>{notice.textContent=text;};
 function source(entry,field,item=''){
  if(!entry.dataset.nativePrefix)return null;
  const named=document.querySelector('[name="'+CSS.escape(entry.dataset.nativePrefix+'-'+field)+'"]');
  return item?named?.closest('.allocation-row')?.querySelector('[data-item-id="'+CSS.escape(item)+'"]'):named;
 }
 function filter(){
  const terms=search.value.trim().toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);let count=0;
  matrix.querySelectorAll('[data-smg-section]').forEach(section=>{
   let matches=0;
   section.querySelectorAll('[data-plan-entry]').forEach(entry=>{
    const deleted=!daily&&source(entry,'DELETE')?.checked;
    const type=section.dataset.smgSection,chosen=name=>filters[name]?.values()||[],matchFilter=(name,value)=>!chosen(name).length||chosen(name).includes(value);
    const group=entry.closest('[data-smg-group]')?.querySelector('summary').textContent||'';
    const extra=type==='work'?matchFilter('section',entry.dataset.sectionKey)&&matchFilter('object',entry.dataset.objectKey||group)&&matchFilter('package',entry.dataset.package)&&(!chosen('work').length||chosen('work').some(v=>v.split('|')[0]===entry.dataset.entryId)):matchFilter(type,entry.dataset.label);
    entry.hidden=deleted||!extra||!terms.every(term=>entry.dataset.name.toLocaleLowerCase('ru').includes(term))||(deviations.checked&&entry.dataset.deviation!=='true');
    if(!entry.hidden){
     matches++;
     const active=terms.length||deviations.checked||Object.values(filters).some(f=>f.values().length);
     if(active&&(!kind.value||kind.value===type))for(let parent=entry.parentElement;parent&&parent!==matrix;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
    }
   });
   section.querySelectorAll('[data-smg-group]').forEach(group=>{group.hidden=![...group.querySelectorAll('[data-plan-entry]')].some(e=>!e.hidden);});
   section.hidden=Boolean(kind.value&&kind.value!==section.dataset.smgSection)||(!matches&&Boolean(terms.length||deviations.checked));
   if(!section.hidden)count+=matches;
  });
  document.getElementById('smg-empty').hidden=count>0||!terms.length&&!kind.value&&!deviations.checked;
 }
 const container=root.querySelector('[data-plan-multi-filters]');
 if(window.graphMultiselect){
  const sets={section:new Map(),object:new Map(),package:new Map(),work:new Map(),equipment:new Map(),labor:new Map()};
  matrix.querySelectorAll('[data-plan-entry]').forEach(entry=>{
   const type=entry.closest('[data-smg-section]').dataset.smgSection,group=entry.closest('[data-smg-group]').querySelector('summary').textContent;
   if(type==='work'){
    sets.section.set(entry.dataset.sectionKey,entry.dataset.sectionLabel||'Без раздела');sets.object.set(entry.dataset.objectKey||group,(entry.dataset.sectionLabel?entry.dataset.sectionLabel+' → ':'')+(entry.dataset.objectLabel||group));if(entry.dataset.package)sets.package.set(entry.dataset.package,entry.dataset.path);
    for(const tr of entry.querySelectorAll('[data-plan-measure]'))if(!entry.dataset.package||tr.dataset.planItem)sets.work.set(entry.dataset.entryId+'|'+tr.dataset.planItem,entry.dataset.package?entry.dataset.path+' → '+tr.dataset.measure:entry.dataset.path);
   }else if(sets[type])sets[type].set(entry.dataset.label,entry.dataset.label);
  });
  for(const [name,label] of [['section','Раздел'],['object','Комплексный объект'],['package','Комплексная работа'],['work','Работа'],['equipment','Техника'],['labor','Должность']]){
   const host=document.createElement('div');host.dataset.planFilter=name;container.append(host);
   filters[name]=window.graphMultiselect.create(host,label,[...sets[name]].map(([value,label])=>({value,label})),filter);
  }
 }
 [search,kind,deviations].forEach(field=>field.addEventListener('input',filter));
 document.getElementById('smg-reset').addEventListener('click',()=>{search.value='';kind.value='';deviations.checked=false;Object.values(filters).forEach(f=>f.set([]));filter();});
 root.querySelectorAll('[data-plan-expand],[data-expand-allocations],[data-collapse-allocations]').forEach(button=>button.addEventListener('click',()=>{
  const open=button.hasAttribute('data-expand-allocations')||button.dataset.planExpand==='true';matrix.querySelectorAll('details').forEach(d=>d.open=open);
 }));
 root.querySelectorAll('[data-plan-fold-level]').forEach(button=>button.addEventListener('click',()=>{
  const level=root.querySelector('[data-plan-level-select]').value,open=button.dataset.planFoldLevel==='true';
  matrix.querySelectorAll('[data-plan-level="'+level+'"]').forEach(node=>{
   node.open=open;if(open)for(let parent=node.parentElement;parent&&parent!==matrix;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  });
 }));
 const foldKey='plan-hierarchy:'+location.pathname;
 let folds={};try{folds=JSON.parse(localStorage.getItem(foldKey)||'{}')||{};}catch{}
 matrix.querySelectorAll('[data-plan-node-key]').forEach(node=>{
  if(Object.hasOwn(folds,node.dataset.planNodeKey))node.open=Boolean(folds[node.dataset.planNodeKey]);
  node.addEventListener('toggle',()=>{folds[node.dataset.planNodeKey]=node.open;try{localStorage.setItem(foldKey,JSON.stringify(folds));}catch{}});
 });
 function canLeave(){
  if(daily)return window.smgSaveUI?.canLeave()??![...matrix.querySelectorAll('.smg-day-input')].some(i=>i.value!==i.dataset.original);
  return window.gprSaveUI?.canLeave()??true;
 }
 window.planWorkbench={canLeave,filter};
 const addDialog=root.querySelector('[data-plan-add-dialog]');
 function resourceKind(value){
  const form=addDialog.querySelector('form'),fields={labor:['brigade','rate'],equipment:['equipment','rate'],fuel:['fuel','rate']};
  form.querySelector('[name=kind]').value=value;
  form.querySelectorAll('[data-add-field]').forEach(wrapper=>{wrapper.hidden=!(fields[value]||[]).includes(wrapper.dataset.addField);});
  addDialog.querySelector('#plan-add-title').textContent='Добавить '+{labor:'должность',equipment:'технику',fuel:'ГСМ'}[value];
 }
 root.querySelectorAll('[data-plan-add-resource]').forEach(button=>button.addEventListener('click',()=>{if(canLeave()){resourceKind(button.dataset.planAddResource);addDialog.showModal();}}));
 root.querySelectorAll('[data-plan-close-dialog]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
 root.querySelector('[data-plan-excel-help]').addEventListener('click',()=>root.querySelector('[data-plan-excel-dialog]').showModal());
 if(addDialog?.querySelector('.errorlist')&&addDialog.querySelector('[name=kind]').value!=='work'){resourceKind(addDialog.querySelector('[name=kind]').value||'labor');addDialog.showModal();}
 // The year/month controls are identical; only their destination URLs differ.
 const picker=root.querySelector('[data-workbench-month-picker]'),year=picker.querySelector('[data-workbench-year]'),month=picker.querySelector('[data-workbench-month]');
 const options=[...month.options].map(o=>({value:o.value,label:o.textContent,month:o.dataset.month})),initial=month.value;
 function build(value){month.replaceChildren();options.filter(o=>o.month.startsWith(year.value+'-')).forEach(o=>{const option=new Option(o.label,o.value);option.dataset.month=o.month;month.add(option);});if(options.some(o=>o.value===value))month.value=value;}
 function navigate(){if(canLeave())location.assign(month.value);else{year.value=options.find(o=>o.value===initial).month.slice(0,4);build(initial);}}
 build(initial);year.addEventListener('change',()=>{build('');navigate();});month.addEventListener('change',navigate);
 // TSV can be pasted into Excel directly. Text labels cannot become formulas.
 function textCell(value){value=String(value??'');if(/^[=+@-]/.test(value))value="'"+value;return /[\t\r\n"]/.test(value)?'"'+value.replaceAll('"','""')+'"':value;}
 function rowNumbers(tr){
  if(daily){
   const cells=[...tr.querySelectorAll('td')];const days=tr.closest('table').querySelectorAll('thead th').length-(deviations.disabled?3:5);
   return cells.slice(-days).map(td=>td.querySelector('input')?.value??td.dataset.value??'0');}

  const td=tr.querySelector('[data-plan-month-value]');return [td?.querySelector('input')?.value??td?.dataset.value??'0'];
 }
 async function clipboard(text){
  try{await navigator.clipboard.writeText(text);}catch{
   const field=document.createElement('textarea');field.value=text;field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();
   const ok=document.execCommand('copy');field.remove();if(!ok)throw new Error('Браузер не разрешил копирование. Разрешите доступ к буферу обмена.');
  }
 }
 root.addEventListener('click',async event=>{
  const button=event.target.closest('[data-plan-copy-row],[data-plan-copy-table]');if(!button)return;
  try{
   if(button.hasAttribute('data-plan-copy-row')){
    const numbers=rowNumbers(button.closest('tr'));await clipboard(numbers.map(value=>String(value).replace('.',',')).join('\t'));message('Скопировано значений: '+numbers.length+'. В Excel выберите первую ячейку и нажмите Ctrl+V.');
   }else{
    if(!canLeave()){message('Сначала сохраните план, чтобы скопировать таблицу с рассчитанными итогами.');return;}
    const rows=[];
    for(const entry of matrix.querySelectorAll('[data-plan-entry]')){
     if(entry.hidden||entry.closest('[data-smg-section]').hidden)continue;
     for(const tr of entry.querySelectorAll('[data-plan-measure]')){const numbers=daily?[...tr.querySelectorAll('td')].map(td=>td.dataset.value??'0'):rowNumbers(tr);rows.push([textCell(entry.dataset.path),textCell(tr.dataset.measure),...numbers.map(value=>String(value).replace('.',','))]);}
    }
    const first=matrix.querySelector('[data-plan-entry]')?.closest('table').querySelector('thead');const headers=daily?[...first?.querySelectorAll('th')||[]].slice(2).map(th=>textCell(th.textContent)):['ГПР за месяц'];
    await clipboard([['Работа / ресурс','Показатель / ед.',...headers],...rows].map(row=>row.join('\t')).join('\r\n'));
    message('Скопировано строк: '+rows.length+'. В Excel выберите первую ячейку и нажмите Ctrl+V.');
   }
  }catch(error){message(error.message);}
 });
 // A single delete gesture; GPR marks the formset row, SMG uses a reviewed POST.
 const remove=root.querySelector('[data-plan-delete-dialog]');let deleteEntry=null;
 root.querySelectorAll('[data-plan-delete]').forEach(button=>button.addEventListener('click',()=>{
  if(daily&&!canLeave())return;deleteEntry=button.closest('[data-plan-entry]');remove.querySelector('[data-plan-delete-name]').textContent=deleteEntry.dataset.label;
  remove.querySelector('[name=entry_id]').value=deleteEntry.dataset.entryId;remove.querySelector('[name=revision]').value=deleteEntry.dataset.revision;remove.showModal();
 }));
 if(!daily)remove.querySelector('form').addEventListener('submit',event=>{event.preventDefault();const row=source(deleteEntry,'DELETE')?.closest('.allocation-row');row?.querySelector('[data-remove-row]').click();deleteEntry.hidden=true;remove.close();filter();message('Строка исключена из плана. Нажмите «Сохранить».');});
 if(!daily){
  root.querySelectorAll('[data-plan-remove-item]').forEach(button=>button.addEventListener('click',()=>{
   const entry=button.closest('[data-plan-entry]'),row=button.closest('tr'),field=source(entry,'quantity',button.dataset.planRemoveItem);
   if(!field)return;
   field.closest('tr').querySelector('button').click();row.remove();
   const title=entry.querySelector('.matrix-label');if(title)title.rowSpan=Math.max(1,title.rowSpan-1);
   syncComputed();filter();message('Работа исключена из состава плана. Нажмите «Сохранить».');
  }));
  function syncComputed(){matrix.querySelectorAll('[data-computed-source]').forEach(td=>{const field=document.querySelector('[name="'+CSS.escape(td.dataset.computedSource)+'"]');if(field){td.dataset.value=field.value;td.querySelector('[data-plan-number]').textContent=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(Number(field.value));}});}
  matrix.addEventListener('input',event=>{
   const input=event.target.closest('[data-plan-month-input]');if(!input)return;
   const field=source(input.closest('[data-plan-entry]'),input.dataset.field,input.dataset.item);
   if(!field){message('Структура работы изменилась. Сохраните или обновите страницу.');return;}
   field.value=input.value;field.dispatchEvent(new Event('input',{bubbles:true}));input.closest('[data-plan-month-value]').dataset.value=input.value;syncComputed();
  });
  native.addEventListener('input',()=>{native.dataset.workbenchSubmitting='false';requestAnimationFrame(syncComputed);});
  native.addEventListener('change',()=>{native.dataset.workbenchSubmitting='false';});
  window.addEventListener('pageshow',()=>{native.dataset.workbenchSubmitting='false';});
  native.addEventListener('submit',event=>{const invalid=[...matrix.querySelectorAll('input')].find(i=>!i.checkValidity());if(invalid){event.preventDefault();for(let node=invalid.parentElement;node&&node!==matrix;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;invalid.reportValidity();}},true);
  const settings=root.querySelector('[data-plan-settings-dialog]');let settingsEntry=null;
  root.querySelectorAll('[data-plan-settings]').forEach(button=>button.addEventListener('click',()=>{
   settingsEntry=button.closest('[data-plan-entry]');const field=source(settingsEntry,'load_profile');
   const select=settings.querySelector('[data-plan-settings-profile]');select.replaceChildren(...[...field.options].map(o=>new Option(o.textContent,o.value)));select.value=field.value;
   settings.querySelector('[data-plan-settings-name]').textContent=settingsEntry.dataset.label;settings.showModal();
  }));
  settings.querySelector('[data-plan-apply-settings]').addEventListener('click',()=>{const field=source(settingsEntry,'load_profile'),value=settings.querySelector('[data-plan-settings-profile]').value;if(field.value!==value){field.value=value;field.dispatchEvent(new Event('change',{bubbles:true}));}settings.close();});
  // Back/refresh protection also covers edits made through the shared surface.
  window.addEventListener('beforeunload',event=>{if(native.dataset.dirty==='true'&&native.dataset.workbenchSubmitting!=='true'){event.preventDefault();event.returnValue='';}});
  native.addEventListener('submit',event=>{if(!event.defaultPrevented)native.dataset.workbenchSubmitting='true';});
  syncComputed();
 }
 filter();
})();
