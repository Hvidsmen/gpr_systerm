(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]'),panel=document.querySelector('[data-production-report]');if(!form||!panel)return;
 const employeeLocations=JSON.parse(document.getElementById('production-employee-locations')?.textContent||'{}');
 const choices=JSON.parse(document.getElementById('production-work-choices').textContent),catalogue=new Map(choices.map(w=>[w.token,w]));let savedWorks=JSON.parse(document.getElementById('production-graph-saved').textContent),savedPeople=JSON.parse(document.getElementById('production-report-people').textContent);
 const reasonText=row=>[...row.querySelectorAll('input[name$="-actuation_reasons"]:checked')].map(i=>i.closest('label').textContent.trim()).join('; ')||row.querySelector('[name$="-actuation_reason"]')?.value||'';
 const shiftLabel=value=>({'1':'День','2':'Ночь'}[String(value)]||'Смена '+value);
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),key=(w,i)=>i?`item:${w}:${i}`:`work:${w}`;let exportRows=[];
 function collect(){const works=new Map(savedWorks.map(w=>[key(w.project_work_id,w.work_item_id),{token:key(w.project_work_id,w.work_item_id),quantity:String(w.actual_quantity),actuationType:w.actuation_type,actuationReason:w.actuation_reason,owner:w.owner||w.reported_by__username||'Общий ввод',draft:false}])),people=new Map(savedPeople.map(p=>['pk:'+p.id,{...p,draft:Boolean(p.pending_links)}]));
  form.querySelectorAll('[data-work-fact-row]').forEach(row=>{const w=field(row,'project_work')?.value;if(!w)return;const token=key(w,field(row,'work_item')?.value);if(field(row,'id')?.value&&!row.dataset.entryDirty&&works.has(token))return;if(field(row,'DELETE')?.checked){works.delete(token);return;}const quantity=field(row,'actual_quantity')?.value.trim();if(quantity){const prior=works.get(token);works.set(token,{token,quantity,actuationType:field(row,'actuation_type')?.value,actuationReason:reasonText(row),owner:'Мой ввод',draft:!prior||Boolean(row.dataset.entryDirty)||Number(quantity.replace(',','.'))!==Number(prior.quantity.replace(',','.'))});}else if(row.dataset.entryDirty)works.delete(token);});
  form.querySelectorAll('[data-production-person]').forEach(row=>{const employee=field(row,'employee');if(!employee?.value)return;const marker=field(row,'id')?.value?'pk:'+field(row,'id').value:employee.name;
   if(!field(row,'id')?.value&&!row.dataset.entryDirty&&!row.dataset.personDirty&&field(row,'person_editing')?.value==='0'&&savedPeople.some(p=>p.employee===Number(employee.value)&&String(p.shift)===String(field(row,'shift').value)))return;
   if(field(row,'id')?.value&&!row.dataset.entryDirty&&!row.dataset.personDirty&&people.has(marker))return;
   if(field(row,'DELETE')?.checked){people.delete(marker);return;}const mode=field(row,'work_mode').value,label=employee.selectedOptions[0]?.textContent||'',parts=label.split(' · '),prior=people.get(marker);
   people.set(marker,{group:prior?.group,group_order:prior?.group_order,employee:Number(employee.value),name:employeeLocations[employee.value]?.name||parts[0],position:field(row,'planning_position')?.selectedOptions[0]?.value?field(row,'planning_position').selectedOptions[0].textContent:(prior?.staff_position??employeeLocations[employee.value]?.position_label??parts[1]??''),shift:field(row,'shift').value,general:mode==='general',reviewed:mode!=='pending',constant:false,unclaimed:!prior&&field(row,'person_editing')?.value==='0',targets:mode==='works'?[...row.querySelectorAll('[name$="-work_targets"]')].map(i=>i.value):[],owner:'Мой ввод',draft:!prior||Boolean(row.dataset.entryDirty||row.dataset.personDirty)});
  });return {works:[...works.values()],people:[...people.values()]};
 }
 function cell(tr,text){const td=document.createElement('td');td.textContent=text;tr.append(td);return td;}
 function groupRow(host,label,span,level){const tr=document.createElement('tr');tr.className='production-report-group production-report-group-'+level;const td=cell(tr,label);td.colSpan=span;host.append(tr);}
 function personGroup(person){const row=[...form.querySelectorAll('[data-production-person]')].find(r=>String(field(r,'employee')?.value)===String(person.employee)&&String(field(r,'shift')?.value)===String(person.shift));return row?.closest('[data-position-group]')?.dataset.positionGroup||person.group||'Без группы плана';}
 function groupOrder(person){const row=[...form.querySelectorAll('[data-production-person]')].find(r=>String(field(r,'employee')?.value)===String(person.employee)&&String(field(r,'shift')?.value)===String(person.shift));return Number(row?.closest('[data-position-group]')?.dataset.positionGroupOrder??person.group_order??6);}
 function render(){const folds=new Map([...panel.querySelectorAll('[data-report-fold]')].map(el=>[el.dataset.reportFold,el.tagName==='DETAILS'?el.open:el.getAttribute('aria-expanded')!=='false']));const {works,people}=collect();works.sort((a,b)=>{const x=catalogue.get(a.token),y=catalogue.get(b.token);return ((x?.object||'')+'|'+(x?.package||'')).localeCompare((y?.object||'')+'|'+(y?.package||''),'ru')||(x?.title||'').localeCompare(y?.title||'','ru');});people.sort((a,b)=>groupOrder(a)-groupOrder(b)||personGroup(a).localeCompare(personGroup(b),'ru')||(a.position||'').localeCompare(b.position||'','ru')||a.name.localeCompare(b.name,'ru'));const workHost=panel.querySelector('[data-report-works]'),peopleHost=panel.querySelector('[data-report-people]'),issues=[],totals=panel.querySelector('[data-report-totals]');workHost.replaceChildren();peopleHost.replaceChildren();totals.replaceChildren();exportRows=[['Раздел','Работа','Сотрудник / должность','Объём','Ед.','Смена','Ввод','Статус']];
  for(const [label,value] of [['Работ',works.length],['Сотрудников',new Set(people.map(p=>p.employee)).size],['Без привязки',new Set(people.filter(p=>p.general).map(p=>p.employee)).size],['Связи не проверены',people.filter(p=>!p.reviewed).length]]){const card=document.createElement('div');card.textContent=label+': ';const strong=document.createElement('strong');strong.textContent=value;card.append(strong);totals.append(card);}
  const workKeys=new Set(works.map(w=>w.token)),seen=new Set();
  for(const person of people){const identity=person.employee+':'+person.shift;if(seen.has(identity))issues.push('Сотрудник указан дважды за смену: '+person.name);seen.add(identity);if(person.unclaimed)issues.push('Дождитесь сохранения сотрудника: '+person.name);if(!person.reviewed)issues.push('Проверьте занятость: '+person.name);for(const token of person.targets)if(!workKeys.has(token))issues.push('Нет объёма работы для '+person.name+': '+(catalogue.get(token)?.label||token));}
  for(const meta of choices){if(meta.display?.has_plan&&!workKeys.has(meta.token))issues.push('Не внесён объём по плану: '+meta.label);}
  const issueHost=panel.querySelector('[data-report-issues]');issueHost.replaceChildren();if(issues.length){issueHost.className='alert alert-warning';const title=document.createElement('strong');title.textContent='Нужно проверить:';const list=document.createElement('ul');list.className='mb-0';for(const text of [...new Set(issues)]){const li=document.createElement('li');li.textContent=text;list.append(li);}issueHost.append(title,list);}else{issueHost.className='small text-success my-3';issueHost.textContent='Связи проверены. Объёмы разных единиц измерения не суммируются.';}

  function section(host,label,fold){const details=document.createElement('details');details.open=folds.get(fold)??true;details.className='production-report-section';details.dataset.reportFold=fold;const summary=document.createElement('summary');summary.textContent=label;details.append(summary);host.append(details);return details;}
  function table(host,headers){const wrap=document.createElement('div');wrap.className='table-responsive';const table=document.createElement('table');table.className='table table-sm production-report-table';const head=document.createElement('thead'),tr=document.createElement('tr');for(const label of headers){const th=document.createElement('th');th.textContent=label;tr.append(th);}head.append(tr);table.append(head);wrap.append(table);host.append(wrap);return table;}
  function linked(entries){const tokens=new Set(entries.map(w=>w.token));return people.filter(p=>p.targets.some(t=>tokens.has(t)));}
  for(const item of window.productionReportGroups.workTree(works,catalogue)){
   const host=section(workHost,item.label,'works:'+item.key),grid=table(host,['Работа / комплексный объект','Объём','Ед.','Сотрудники и смены','Ввод']);
   for(const [index,work] of item.works.entries()){
    const total=document.createElement('tbody'),tr=document.createElement('tr');tr.className='production-report-total';tr.dataset.workTotal='';
    const name=cell(tr,''),toggle=document.createElement('button');toggle.type='button';toggle.className='production-report-toggle';toggle.dataset.reportToggle='work-'+index;toggle.dataset.reportFold='work:'+item.key+':'+work.key;toggle.setAttribute('aria-expanded',String(folds.get(toggle.dataset.reportFold)??true));toggle.textContent=work.label;name.append(toggle);
    cell(tr,window.productionReportGroups.quantity(work.entries));cell(tr,work.unit);cell(tr,linked(work.entries).map(p=>p.name+' · '+shiftLabel(p.shift)).join('; ')||'Сотрудники не привязаны');cell(tr,work.entries.some(w=>w.draft)?'Есть несохранённые записи':'Сохранено');total.append(tr);grid.append(total);
    const branch=document.createElement('tbody');branch.dataset.reportBranch='work-'+index;branch.hidden=toggle.getAttribute('aria-expanded')==='false';grid.append(branch);
    for(const object of work.objects){const row=document.createElement('tr'),title=cell(row,object.label);title.className='production-report-leaf';
     for(const entry of object.entries){if(entry.actuationType){const note=document.createElement('div');note.className='small text-warning-emphasis';note.textContent='Актировка: '+({DAY:'Дневная',NIGHT:'Ночная',FULL_DAY:'Суточная'}[entry.actuationType]||entry.actuationType)+' · '+entry.actuationReason;title.append(note);}}
     cell(row,window.productionReportGroups.quantity(object.entries));cell(row,work.unit);cell(row,linked(object.entries).map(p=>p.name+' · '+shiftLabel(p.shift)).join('; ')||'Сотрудники не привязаны');cell(row,[...new Set(object.entries.map(w=>w.owner+' · '+(w.draft?'Не сохранено':'Сохранено')))].join('; '));branch.append(row);
    }
   }
  }
  const groups=new Map();for(const person of people){const group=personGroup(person);if(!groups.has(group))groups.set(group,[]);groups.get(group).push(person);}
  for(const [group,persons] of groups){
   const host=section(peopleHost,group+' · Сотрудников: '+new Set(persons.map(p=>p.employee)).size,'people:'+group);let position=null,body=null;
   for(const person of persons){if(person.position!==position||!body){const title=document.createElement('h4');title.className='h6 production-report-group-role';title.textContent=person.position||'Должность не указана';host.append(title);const grid=table(host,['Сотрудник','Смена','Выполненные работы / занятость','Ввод']);body=document.createElement('tbody');grid.append(body);position=person.position;}
    const tr=document.createElement('tr'),assignment=!person.reviewed?'Связи пока не проверены':person.general?'Работал без привязки к работе':person.targets.map(t=>catalogue.get(t)?.label||t).join('; '),status=person.draft?'Не сохранено':'Сохранено';cell(tr,person.name);cell(tr,shiftLabel(person.shift));cell(tr,assignment);cell(tr,person.owner+' · '+status+(person.constant?' · постоянный персонал':''));body.append(tr);
   }
  }
  if(!works.length)workHost.textContent='Объёмы работ пока не внесены.';if(!people.length)peopleHost.textContent='Сотрудники пока не добавлены.';

 }
 let renderPending=false;function scheduleRender(){if(panel.hidden||renderPending)return;renderPending=true;requestAnimationFrame(()=>{renderPending=false;render();});}
 form.addEventListener('production-report-update',event=>{savedWorks=event.detail.works;savedPeople=event.detail.people;scheduleRender();});
 form.addEventListener('production-step-change',event=>{if(event.detail===4){scheduleRender();loadEquipment();}});form.addEventListener('change',scheduleRender);
 panel.querySelector('[data-report-download]').addEventListener('click',async event=>{
  const button=event.currentTarget,status=panel.querySelector('[data-report-download-status]');button.disabled=true;
  try{
   if(window.productionAutoSave&&!await window.productionAutoSave.flush())throw new Error('Исправьте ошибки сохранения перед скачиванием отчёта.');
   if(form.productionLinks&&!await form.productionLinks.flush())throw new Error('Дождитесь отправки связей перед скачиванием отчёта. Черновик сохранён в браузере.');
   status.textContent='Подготовка отчёта…';const url=button.dataset.url+'?'+new URLSearchParams({construction_object:form.elements.construction_object.value,date:form.elements.date.value});
   const response=await fetch(url,{credentials:'same-origin'});if(!response.ok||!response.headers.get('content-type')?.includes('spreadsheetml'))throw new Error('Не удалось сформировать Excel. Повторите скачивание.');
   const blob=await response.blob(),link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='Производство_'+form.elements.date.value+'.xlsx';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),30000);status.textContent='Отчёт скачан.';
  }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
 });
 let equipmentBusy=false;
 async function loadEquipment(){
  if(equipmentBusy)return;equipmentBusy=true;const button=panel.querySelector('[data-daily-equipment-refresh]'),status=panel.querySelector('[data-daily-equipment-status]');button.disabled=true;status.textContent='Загрузка техники…';
  try{const query=new URLSearchParams({construction_object:form.elements.construction_object.value,date:form.elements.date.value,scope:'day'});const response=await fetch(button.dataset.url+'?'+query,{credentials:'same-origin',cache:'no-store'});if(!response.ok||response.redirected)throw new Error('Не удалось обновить технику. Повторите загрузку.');panel.querySelector('[data-daily-equipment]').innerHTML=await response.text();status.textContent='Техника обновлена за выбранный день.';}
  catch(error){status.textContent=error.message;}finally{equipmentBusy=false;button.disabled=false;}
 }
 panel.querySelector('[data-daily-equipment-refresh]').addEventListener('click',loadEquipment);
 if(!panel.hidden){render();loadEquipment();}
})();
