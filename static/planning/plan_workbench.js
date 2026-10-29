/* Shared editing surface. GPR binds to its existing formset; SMG uses daily cells. */
(() => {
 'use strict';
 const root=document.querySelector('[data-plan-workbench]');if(!root)return;
 const daily=root.dataset.planWorkbench==='smg',matrix=root.querySelector('[data-plan-matrix]');
 // Account for the live header height, including workflow and save messages.
 const header=root.querySelector('.plan-action-header');
 function positionAddBar(){
  if(!header)return;
  const top=parseFloat(getComputedStyle(header).top)||0;
  root.style.setProperty('--plan-workbench-add-top',(top+header.getBoundingClientRect().height+6)+'px');
 }
 if(header&&window.ResizeObserver)new ResizeObserver(positionAddBar).observe(header);
 window.addEventListener('resize',positionAddBar);positionAddBar();
 const native=document.getElementById('allocation-editor');
 const search=document.getElementById('smg-search'),kind=document.getElementById('smg-kind'),deviations=document.getElementById('smg-deviations');
 const notice=root.querySelector('[data-plan-copy-status]');
 const filters={};
 const message=text=>{notice.textContent=text;};
 function source(entry,field,item=''){
  if(!entry.dataset.nativePrefix)return null;
  const named=native?.elements.namedItem(entry.dataset.nativePrefix+'-'+field);
  return item?named?.closest('.allocation-row')?.querySelector('[data-item-id="'+CSS.escape(item)+'"]'):named;
 }
 function filter(){
  const terms=search.value.trim().toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);let count=0;
  matrix.querySelectorAll('[data-smg-section]').forEach(section=>{
   let matches=0;
   section.querySelectorAll('[data-plan-entry]').forEach(entry=>{
    const deleted=entry.dataset.crewRemoval==='true'||!daily&&source(entry,'DELETE')?.checked;
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
  refreshResourceTotals();
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
  const form=addDialog.querySelector('form'),fields={labor:['brigade','rate'],equipment:['equipment','shifts_per_day','add_crew','rate'],fuel:['fuel','rate']};
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
 const remove=root.querySelector('[data-plan-delete-dialog]');let deleteEntry=null,deleteMachines=[];
 const removalField=remove.querySelector('[name=crew_removal_actions]');
 let removalDecisions={};try{
  const parsed=JSON.parse(removalField.value||'{}');
  if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))removalDecisions=Object.fromEntries(Object.entries(parsed).filter(([pk,a])=>/^\d+$/.test(pk)&&a&&['detach','remove_equipment'].includes(a.mode)&&a.equipment&&typeof a.equipment==='object'));
 }catch{}
 let nativeRemovalField=null;
 if(!daily){nativeRemovalField=removalField.cloneNode();native.append(nativeRemovalField);}
 function machineId(entry){return daily?entry.dataset.entryId:source(entry,'id')?.value;}
 function activeMachines(brigade){
  return [...matrix.querySelectorAll('[data-smg-section=equipment] [data-plan-entry]')].filter(gear=>
   gear.dataset.crewRemoval!=='true'&&!(source(gear,'DELETE')?.checked)&&machineId(gear)&&
   JSON.parse(gear.dataset.crewRecipe||'[]').some(member=>String(member.brigade_id)===String(brigade)));
 }
 function presentRemoval(brigade,action){
  for(const gear of activeMachines(brigade)){
   if(action.mode==='remove_equipment')gear.dataset.crewRemoval='true';
   else gear.dataset.crewRecipe=JSON.stringify(JSON.parse(gear.dataset.crewRecipe||'[]').filter(member=>String(member.brigade_id)!==String(brigade)));
  }
 }
 if(!daily)for(const [brigade,action] of Object.entries(removalDecisions).sort((a,b)=>(a[1].order||0)-(b[1].order||0)))presentRemoval(brigade,action);
 root.querySelectorAll('[data-plan-delete]').forEach(button=>button.addEventListener('click',()=>{
  if(daily&&!canLeave())return;deleteEntry=button.closest('[data-plan-entry]');remove.querySelector('[data-plan-delete-name]').textContent=deleteEntry.dataset.label;
  const labor=deleteEntry.closest('[data-smg-section]').dataset.smgSection==='labor';
  deleteMachines=labor?activeMachines(deleteEntry.dataset.brigadeId):[];
  remove.querySelector('[data-plan-delete-crew]').hidden=!deleteMachines.length;
  remove.querySelector('[data-plan-confirm-delete]').hidden=Boolean(deleteMachines.length);
  remove.querySelectorAll('[data-plan-delete-mode]').forEach(button=>button.hidden=!deleteMachines.length);
  remove.querySelector('[data-plan-delete-machines]').replaceChildren(...deleteMachines.map(gear=>{const li=document.createElement('li');li.textContent=gear.dataset.label;return li;}));
  remove.querySelector('[name=entry_id]').value=deleteEntry.dataset.entryId;remove.querySelector('[name=revision]').value=deleteEntry.dataset.revision;remove.showModal();
 }));
 remove.querySelector('form').addEventListener('submit',event=>{
  const mode=event.submitter?.dataset.planDeleteMode;
  if(deleteMachines.length&&!mode){event.preventDefault();return;}
  if(mode){
   const equipment=Object.fromEntries(deleteMachines.map(gear=>[machineId(gear),daily?gear.dataset.revision:'']));
   removalDecisions[deleteEntry.dataset.brigadeId]={mode,equipment,order:Object.keys(removalDecisions).length};
   removalField.value=JSON.stringify(removalDecisions);
  }
  if(daily)return;
  event.preventDefault();
  if(deleteEntry.closest('[data-smg-section]').dataset.smgSection==='equipment'){
   for(const action of Object.values(removalDecisions))delete action.equipment[machineId(deleteEntry)];
   removalField.value=JSON.stringify(removalDecisions);
  }
  nativeRemovalField.value=removalField.value;
  if(mode)presentRemoval(deleteEntry.dataset.brigadeId,removalDecisions[deleteEntry.dataset.brigadeId]);
  const row=source(deleteEntry,'DELETE')?.closest('.allocation-row');row?.querySelector('[data-remove-row]').click();
  deleteEntry.hidden=true;remove.close();syncTableCrew();filter();
  message(mode==='remove_equipment'?'Должность и связанная техника исключены из плана. Нажмите «Сохранить».':mode==='detach'?'Должность исключена из плана, техника остаётся. Нажмите «Сохранить».':'Строка исключена из плана. Нажмите «Сохранить».');
 });
 if(!daily){
  root.querySelectorAll('[data-plan-remove-item]').forEach(button=>button.addEventListener('click',()=>{
   const entry=button.closest('[data-plan-entry]'),row=button.closest('tr'),field=source(entry,'quantity',button.dataset.planRemoveItem);
   if(!field)return;
   field.closest('tr').querySelector('button').click();row.remove();
   const title=entry.querySelector('.matrix-label');if(title)title.rowSpan=Math.max(1,title.rowSpan-1);
   syncComputed();filter();message('Работа исключена из состава плана. Нажмите «Сохранить».');
  }));
  function syncComputed(){matrix.querySelectorAll('[data-computed-source],[data-hidden-computed-source]').forEach(td=>{const field=native?.elements.namedItem(td.dataset.computedSource||td.dataset.hiddenComputedSource);if(field&&td.dataset.value!==field.value){td.dataset.value=field.value;td.querySelector('[data-plan-number]').textContent=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(Number(field.value));}});}
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
 // Resource subtotals use the same visible rows as the table, including crews once.
 function refreshResourceTotals(){
  const format=value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(value);
  const ceiling=value=>Math.ceil(value-Number.EPSILON*Math.max(1,Math.abs(value))*8);
  const numeric=value=>{const n=Number(value);return Number.isFinite(n)?n:0;};
  const filtered=Boolean(search.value.trim()||deviations.checked||Object.values(filters).some(f=>f.values().length));
  function sum(entries){
   const result={before:0,after:0,days:new Map()};
   for(const entry of entries){
    if(entry.hidden||!daily&&source(entry,'DELETE')?.checked)continue;
    const row=entry.querySelector('[data-plan-measure]');if(!row)continue;
    const cells=[...row.querySelectorAll('[data-smg-day-cell]')];
    if(daily&&cells.length){
     let before=0,after=0;
     cells.forEach(cell=>{const b=numeric(cell.dataset.before),a=numeric(cell.querySelector('input')?.value??cell.dataset.value);before+=b;after+=a;
      const value=result.days.get(cell.dataset.date)||{before:0,after:0};value.before+=b;value.after+=a;result.days.set(cell.dataset.date,value);});
     result.before+=before/cells.length;result.after+=after/cells.length;
    }else{
     const cell=row.querySelector('[data-plan-month-value]');result.after+=numeric(cell?.querySelector('input')?.value??cell?.dataset.value);
    }
   }
   return result;
  }
  function write(target,value){
   const set=(selector,n)=>target.querySelectorAll(selector).forEach(cell=>{cell.textContent=format(n);cell.dataset.value=String(n);});
   set('[data-plan-total-before]',ceiling(value.before));set('[data-plan-total-after]',ceiling(value.after));set('[data-plan-total-delta]',value.after-value.before);
   target.querySelectorAll('[data-plan-total-delta]').forEach(cell=>{cell.classList.toggle('text-success',value.after>value.before);cell.classList.toggle('text-danger',value.after<value.before);});
   target.querySelectorAll('[data-plan-total-date]').forEach(cell=>{const day=value.days.get(cell.dataset.planTotalDate)||{before:0,after:0};cell.textContent=format(day.after);cell.dataset.value=String(day.after);cell.dataset.before=String(day.before);const differs=target.dataset.totalReference==='true'&&Math.abs(day.after-day.before)>1e-9;cell.classList.toggle('table-warning',differs);if(differs){const hint=document.createElement('small');hint.textContent='ГПР: '+format(day.before);cell.append(hint);}});
   target.querySelectorAll('[data-plan-total-caption]').forEach(caption=>{caption.dataset.fullCaption??=caption.textContent;caption.textContent=filtered?'Итого по фильтру'+(target.dataset.totalUnit?' · '+target.dataset.totalUnit:''):caption.dataset.fullCaption;});
  }
  matrix.querySelectorAll('[data-smg-section=labor],[data-smg-section=equipment]').forEach(section=>{
   section.querySelectorAll('[data-smg-group]').forEach(group=>{
    const target=group.querySelector('[data-plan-group-totals]');if(!target)return;
    const value=sum(group.querySelectorAll('[data-plan-entry]'));write(target,value);
    const number=group.querySelector('[data-plan-group-total-number]');if(number)number.textContent=format(ceiling(value.after));
    const caption=group.querySelector('[data-plan-group-total-caption]');if(caption&&!daily){caption.dataset.fullCaption??=caption.textContent;caption.textContent=filtered?'По фильтру':caption.dataset.fullCaption;}
   });
   const overall=section.querySelector('[data-plan-section-totals]');if(overall)write(overall,sum(section.querySelectorAll('[data-plan-entry]')));
  });
 }
 let totalsScheduled=false;
 matrix.addEventListener('input',()=>{if(totalsScheduled)return;totalsScheduled=true;requestAnimationFrame(()=>{totalsScheduled=false;refreshResourceTotals();});});
 // A monthly headcount is a fill value, repeated on every editable day.
 function refreshMonthFill(row){
  const fill=row.querySelector('[data-smg-fill-month]');if(!fill)return;
  const inputs=[...row.querySelectorAll('.smg-day-input:not(:disabled)')],values=inputs.map(input=>input.value);
  fill.value=values.length&&values.every(value=>value===values[0])?values[0]:'';
  fill.min=String(Math.max(0,...inputs.map(input=>Number(input.min)||0)));
  const cells=[...row.querySelectorAll('[data-smg-day-cell]')];
  const average=cells.reduce((sum,cell)=>sum+Number(cell.querySelector('input')?.value||cell.dataset.value||0),0)/Math.max(1,cells.length);
  row.querySelector('[data-smg-month-average]').textContent=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(Math.ceil(average-Number.EPSILON*Math.max(1,Math.abs(average))*8));
 }
 if(daily){
  matrix.addEventListener('input',event=>{
   const fill=event.target.closest('[data-smg-fill-month]');
   if(fill){
    if(!fill.value)return;
    if(!fill.checkValidity()){message('Для заполнения месяца введите целое количество не меньше численности экипажа.');return;}
    const row=fill.closest('tr'),inputs=[...row.querySelectorAll('.smg-day-input:not(:disabled)')];
    inputs.forEach(input=>input.value=fill.value);
    inputs.forEach(input=>input.dispatchEvent(new Event('input',{bubbles:true})));
    message('Количество повторено на все открытые дни. Нажмите «Сохранить».');
   }else if(event.target.matches('.smg-day-input')){
    event.target.closest('td').dataset.value=event.target.value;
    refreshMonthFill(event.target.closest('tr'));
   }
  });
 }
 // A position shows its total: captured machine crews plus separately planned people.
 let crewUpdating=false;
 const readJSON=(entry,name,fallback)=>{try{return JSON.parse(entry.dataset[name]||JSON.stringify(fallback));}catch{return fallback;}};
 function syncTableCrew(){
  if(crewUpdating)return;crewUpdating=true;
  try{
   const totals=new Map();
   for(const gear of matrix.querySelectorAll('[data-smg-section=equipment] [data-plan-entry]')){
    if(gear.dataset.crewRemoval==='true'||!daily&&source(gear,'DELETE')?.checked)continue;
    const members=readJSON(gear,'crewRecipe',[]);if(!members.length)continue;
    const values=daily?readJSON(gear,'crewValues',{}):{month:gear.querySelector('[data-field=count]')?.value??gear.querySelector('[data-plan-month-value]')?.dataset.value??0};
    if(daily)gear.querySelectorAll('[data-field=planned_count]').forEach(input=>values[input.dataset.date]=input.value);
    for(const [day,value] of Object.entries(values)){
     const count=Number(value);if(!Number.isInteger(count)||count<0)return;
     for(const member of members){const key=String(member.brigade_id)+'|'+day;totals.set(key,(totals.get(key)||0)+count*member.count);}
    }
   }
   for(const people of matrix.querySelectorAll('[data-smg-section=labor] [data-plan-entry][data-brigade-id]')){
    if(!daily&&source(people,'DELETE')?.checked)continue;
    if(!people.dataset.brigadeId)continue;
    const cache=readJSON(people,'crewCounts',{});
    for(const input of people.querySelectorAll(daily?'[data-field=planned_count]':'[data-field=count]')){
     const day=daily?input.dataset.date:'month',before=Number(cache[day]||0),after=totals.get(people.dataset.brigadeId+'|'+day)||0;
     const current=Number(input.value);if(!Number.isFinite(current)||current<0)continue;
     const extra=Math.max(0,current-before),total=extra+after;input.min=String(after);input.title='Из экипажа: '+after+'. Дополнительно: '+extra+'.';
     cache[day]=after;
     if(String(total)!==input.value){input.value=String(total);input.dispatchEvent(new Event('input',{bubbles:true}));}
     if(after||before){
      let note=people.querySelector('[data-crew-note]');if(!note){note=document.createElement('small');note.className='d-block text-muted';note.dataset.crewNote='';people.querySelector('.matrix-label strong').after(note);}
      note.textContent=daily?'Численность включает экипаж техники. Можно добавить людей сверх экипажа.':'Экипаж техники: '+after+' чел. · Дополнительно: '+extra+' чел.';
     }
    }
    people.dataset.crewCounts=JSON.stringify(cache);
   }
  }finally{crewUpdating=false;}
 }
 matrix.addEventListener('input',event=>{if(event.target.closest('[data-smg-section=equipment]'))syncTableCrew();});
 requestAnimationFrame(()=>{syncTableCrew();if(daily)matrix.querySelectorAll('[data-smg-fill-month]').forEach(fill=>refreshMonthFill(fill.closest('tr')));refreshResourceTotals();});
 filter();
})();

// Keep the common monthly editor heading aligned while the tree changes depth.
(()=>{const grid=document.querySelector('[data-gpr-editor-grid]');if(!grid)return;
 const matrix=grid.querySelector('[data-plan-matrix]'),action=document.querySelector('.plan-action-header'),add=document.querySelector('.plan-workbench-add');
 let queued=false;function layout(){queued=false;grid.style.setProperty('--gpr-grid-width',grid.clientWidth+'px');let top=60;if(action)top=(parseFloat(getComputedStyle(action).top)||0)+action.getBoundingClientRect().height+6;if(add)top+=add.getBoundingClientRect().height+6;grid.style.setProperty('--gpr-header-top',top+'px');const origin=grid.getBoundingClientRect().left;matrix.querySelectorAll('.matrix-scroll').forEach(el=>{if(!el.getClientRects().length)return;const inset=Math.max(0,el.getBoundingClientRect().left-origin+(parseFloat(el.style.getPropertyValue('--gpr-inset'))||0));el.style.setProperty('--gpr-inset',inset+'px');});}
 function schedule(){if(!queued){queued=true;requestAnimationFrame(layout);}}window.addEventListener('resize',schedule);matrix.addEventListener('toggle',schedule,true);new MutationObserver(schedule).observe(matrix,{subtree:true,attributes:true,attributeFilter:['hidden','open']});if(window.ResizeObserver){const o=new ResizeObserver(schedule);[grid,action,add].filter(Boolean).forEach(el=>o.observe(el));}layout();})();

// Package actions remain beside its title when completion moves into the heading.
(()=>{const root=document.querySelector('[data-plan-workbench="gpr"]');if(!root)return;root.querySelectorAll('[data-plan-completion-row]').forEach(row=>{const summary=row.closest('[data-plan-level="package"]')?.querySelector(':scope > summary');if(!summary)return;const tools=document.createElement('span');tools.className='plan-package-actions';row.querySelectorAll('.plan-row-actions button').forEach(original=>{const proxy=document.createElement('button');proxy.type='button';proxy.className=original.className;proxy.textContent=original.textContent;proxy.dataset.packageAction='';proxy.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();original.click();});tools.append(proxy);});summary.append(tools);});})();
