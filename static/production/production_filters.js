(()=>{
 'use strict';const form=document.querySelector('[data-production-form]');if(!form)return;
 const choices=JSON.parse(document.getElementById('production-work-choices').textContent),workMeta=JSON.parse(document.getElementById('production-work-meta')?.textContent||'[]'),catalogue=new Map(choices.map(w=>[w.token,w])),metaById=new Map(workMeta.map(w=>[String(w.id),w]));
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),token=row=>field(row,'operational_name')?.value.trim()?`operational:${row.dataset.factId||field(row,'operational_name').name}`:row.dataset.workToken||(field(row,'work_item')?.value?`item:${field(row,'project_work')?.value}:${field(row,'work_item').value}`:`work:${field(row,'project_work')?.value}`);
 const roleOf=row=>field(row,'planning_position')?.value||String(JSON.parse(document.getElementById('position-employee-roles').textContent)[field(row,'employee')?.value]||'');
 const roleName=row=>field(row,'planning_position')?.selectedOptions[0]?.value?field(row,'planning_position').selectedOptions[0].textContent:row.closest('[data-position-plan]')?.querySelector('h4')?.textContent||'';
 const groupOf=row=>row.closest('[data-position-group]')?.dataset.positionGroup||'Без группы плана';
 let refreshPending=false;
 function scheduleRefresh(){if(refreshPending)return;refreshPending=true;requestAnimationFrame(()=>{refreshPending=false;refresh();});}
 function widget(host,label,options){return window.graphMultiselect.create(host,label,options,scheduleRefresh);}
 const options=values=>[...new Map(values.filter(v=>v.value).map(v=>[String(v.value),{value:String(v.value),label:v.label}])).values()].sort((a,b)=>a.label.localeCompare(b.label,'ru'));
 const filterStorageKey=`production-work-filter:${form.elements.construction_object?.value||''}:${form.elements.date?.value||''}`;let quickWork=sessionStorage.getItem(filterStorageKey)||'all';const filters={};
 for(const kind of ['works','people','graph']){
  const host=document.querySelector(`[data-production-filters="${kind}"]`);if(!host)continue;
  filters[kind]={};
  if(kind!=='people'){
   filters[kind].object=widget(host,'Комплексные объекты',options(workMeta.filter(w=>w.complex_object_id).map(w=>({value:w.complex_object_id,label:w.complex_object__name}))));
   filters[kind].package=widget(host,'Комплексы работ',options(workMeta.filter(w=>w.kind==='COMPOSITE').map(w=>({value:w.id,label:w.name}))));
   filters[kind].work=widget(host,'Работы в составе комплекса',options(choices.map(w=>({value:w.token,label:w.title||w.label}))));
  }
  if(kind!=='works'){
   filters[kind].group=widget(host,'Группы должностей',[]);filters[kind].role=widget(host,'Должности',[]);filters[kind].employee=widget(host,'Люди',[]);
  }
 }
 function selected(widget,value){const values=widget?.values()||[];return !values.length||values.includes(String(value));}
 function rowChoice(row,key){const saved=catalogue.get(key);if(saved)return saved;const work=field(row,'project_work')?.value,meta=metaById.get(String(work)),name=field(row,'operational_name')?.value.trim();if(!work||!meta)return null;const choice={token:key,work:Number(work),item:null,label:name||meta.name,title:name||meta.name,kind:name?'OPERATIONAL':meta.kind,complex_id:field(row,'complex_object_filter')?.value||meta.complex_object_id,complex:meta.complex_object__name||'',package:meta.name};if(name)catalogue.set(key,choice);return choice;}
 function workMatches(key,kind='graph',choice=null){const w=choice||catalogue.get(key),f=filters[kind];if(!f||!w)return true;return selected(f.object,w.complex_id)&&selected(f.package,w.kind==='COMPOSITE'||w.kind==='OPERATIONAL'?w.work:'')&&selected(f.work,key);}
 function personMatches(row,kind='graph'){const f=filters[kind];if(!f)return true;return selected(f.group,groupOf(row))&&selected(f.role,roleOf(row))&&selected(f.employee,field(row,'employee')?.value);}
 function numeric(value){const number=Number(String(value??'').replace(',','.'));return Number.isFinite(number)?number:null;}
 function quickWorkMatches(row){
  const quantity=field(row,'actual_quantity')?.value?.trim()||'',plan=numeric(row.dataset.planValue),deviation=numeric(row.dataset.deviationValue);
  if(quickWork==='day-plan')return plan!==null&&plan>0;
  if(quickWork==='actual')return quantity!=='';
  if(quickWork==='empty')return quantity==='';
  if(quickWork==='unplanned')return row.dataset.planned!=='1';
  if(quickWork==='deviation')return deviation!==null&&deviation!==0;
  return true;
 }
 function syncPeople(){const people=[...form.querySelectorAll('[data-production-person]')].filter(r=>field(r,'employee')?.value&&!field(r,'DELETE')?.checked),roles=options(people.map(row=>({value:roleOf(row),label:roleName(row)||field(row,'employee').selectedOptions[0]?.textContent.split(' · ')[2]||'Без должности'}))),groups=[...form.querySelectorAll('[data-position-group]')].map(g=>({value:g.dataset.positionGroup,label:g.dataset.positionGroup})),employees=options(people.map(row=>({value:field(row,'employee').value,label:field(row,'employee').selectedOptions[0]?.textContent.split(' · ')[0]||''})));
  for(const f of Object.values(filters)){if(f.employee){f.group.setOptions(groups);f.role.setOptions(roles);f.employee.setOptions(employees);}}
 }
 function refresh(){
  const query=(document.querySelector('[data-work-search]')?.value||'').toLocaleLowerCase('ru').trim();
  form.querySelectorAll('[data-work-fact-row]').forEach(row=>{const key=token(row),w=rowChoice(row,key);row.hidden=!quickWorkMatches(row)||Boolean(w&&(!workMatches(key,'works',w)||!query.split(/\s+/).every(term=>w.label.toLocaleLowerCase('ru').includes(term))));});
  form.querySelectorAll('[data-work-package],[data-work-object]').forEach(group=>{group.hidden=[...group.querySelectorAll('[data-work-fact-row]')].every(row=>row.hidden);if(query||Object.values(filters.works||{}).some(w=>w.values().length))group.open=true;});
  form.querySelectorAll('[data-production-person]').forEach(row=>{row.hidden=Boolean(field(row,'employee')?.value&&!personMatches(row,'people'));});
  form.querySelectorAll('[data-position-plan]').forEach(role=>{const people=[...role.querySelectorAll('[data-production-person]')];role.hidden=Boolean(Object.values(filters.people||{}).some(w=>w.values().length)&&!people.some(row=>!row.hidden&&field(row,'employee')?.value));});
  form.querySelectorAll('[data-position-group]').forEach(group=>{group.hidden=[...group.querySelectorAll('[data-position-plan]')].every(role=>role.hidden);});
  form.dispatchEvent(new Event('production-filter-change'));
 }
 form.productionFilters={workMatches,personMatches,syncGraph:()=>{}};
 document.querySelector('[data-work-search]')?.addEventListener('input',scheduleRefresh);
 document.querySelectorAll('[data-work-quick]').forEach(button=>{const active=button.dataset.workQuick===quickWork;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));button.addEventListener('click',()=>{quickWork=button.dataset.workQuick;sessionStorage.setItem(filterStorageKey,quickWork);document.querySelectorAll('[data-work-quick]').forEach(candidate=>{const selected=candidate===button;candidate.classList.toggle('active',selected);candidate.setAttribute('aria-pressed',String(selected));});scheduleRefresh();});});
 form.addEventListener('production-work-created',event=>{choices.push(event.detail);catalogue.set(event.detail.token,event.detail);for(const f of Object.values(filters))if(f.work)f.work.setOptions(options(choices.map(w=>({value:w.token,label:w.title||w.label}))));scheduleRefresh();});
 form.addEventListener('production-report-update',()=>{syncPeople();scheduleRefresh();});form.addEventListener('production-live-applied',()=>{syncPeople();scheduleRefresh();});form.addEventListener('change',event=>{if(event.target.closest('[data-production-person]')){syncPeople();scheduleRefresh();}else if(event.target.closest('[data-work-fact-row]'))scheduleRefresh();});
 refresh();form.addEventListener('production-background-ready',()=>{syncPeople();scheduleRefresh();},{once:true});
})();
