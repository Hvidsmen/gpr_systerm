(()=>{
 'use strict';const form=document.querySelector('[data-production-form]');if(!form)return;
 const choices=JSON.parse(document.getElementById('production-work-choices').textContent),catalogue=new Map(choices.map(w=>[w.token,w]));
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),token=row=>field(row,'work_item')?.value?`item:${field(row,'project_work')?.value}:${field(row,'work_item').value}`:`work:${field(row,'project_work')?.value}`;
 const roleOf=row=>field(row,'planning_position')?.value||String(JSON.parse(document.getElementById('position-employee-roles').textContent)[field(row,'employee')?.value]||'');
 const roleName=row=>field(row,'planning_position')?.selectedOptions[0]?.value?field(row,'planning_position').selectedOptions[0].textContent:row.closest('[data-position-plan]')?.querySelector('h4')?.textContent||'';
 const groupOf=row=>row.closest('[data-position-group]')?.dataset.positionGroup||'Без группы плана';
 function widget(host,label,options){return window.graphMultiselect.create(host,label,options,refresh);}
 const options=values=>[...new Map(values.filter(v=>v.value).map(v=>[String(v.value),{value:String(v.value),label:v.label}])).values()].sort((a,b)=>a.label.localeCompare(b.label,'ru'));
 const filters={};
 for(const kind of ['works','people','graph']){
  const host=form.querySelector(`[data-production-filters="${kind}"]`);if(!host)continue;
  filters[kind]={};
  if(kind!=='people'){
   filters[kind].object=widget(host,'Объекты',options(choices.map(w=>({value:w.object,label:w.object}))));
   filters[kind].package=widget(host,'Комплексные работы',options(choices.filter(w=>w.package).map(w=>({value:w.package,label:w.package}))));
   filters[kind].work=widget(host,'Работы',options(choices.map(w=>({value:w.token,label:w.label}))));
  }
  if(kind!=='works'){
   filters[kind].group=widget(host,'Группы должностей',[]);filters[kind].role=widget(host,'Должности',[]);filters[kind].employee=widget(host,'Люди',[]);
  }
 }
 function selected(widget,value){const values=widget?.values()||[];return !values.length||values.includes(String(value));}
 function workMatches(key,kind='graph'){const w=catalogue.get(key),f=filters[kind];if(!f||!w)return true;return selected(f.object,w.object)&&selected(f.package,w.package)&&selected(f.work,key);}
 function personMatches(row,kind='graph'){const f=filters[kind];if(!f)return true;return selected(f.group,groupOf(row))&&selected(f.role,roleOf(row))&&selected(f.employee,field(row,'employee')?.value);}
 function syncPeople(){const people=[...form.querySelectorAll('[data-production-person]')].filter(r=>field(r,'employee')?.value&&!field(r,'DELETE')?.checked),roles=options(people.map(row=>({value:roleOf(row),label:roleName(row)||field(row,'employee').selectedOptions[0]?.textContent.split(' · ')[2]||'Без должности'}))),groups=options([...form.querySelectorAll('[data-position-group]')].map(g=>({value:g.dataset.positionGroup,label:g.dataset.positionGroup}))),employees=options(people.map(row=>({value:field(row,'employee').value,label:field(row,'employee').selectedOptions[0]?.textContent.split(' · ')[0]||''})));
  for(const f of Object.values(filters)){if(f.employee){f.group.setOptions(groups);f.role.setOptions(roles);f.employee.setOptions(employees);}}
 }
 function refresh(){
  const query=(form.querySelector('[data-work-search]')?.value||'').toLocaleLowerCase('ru').trim();
  form.querySelectorAll('[data-work-fact-row]').forEach(row=>{const key=token(row),w=catalogue.get(key);row.hidden=Boolean(w&&(!workMatches(key,'works')||!query.split(/\s+/).every(term=>w.label.toLocaleLowerCase('ru').includes(term))));});
  form.querySelectorAll('[data-work-package],[data-work-object]').forEach(group=>{group.hidden=[...group.querySelectorAll('[data-work-fact-row]')].every(row=>row.hidden);if(query||Object.values(filters.works||{}).some(w=>w.values().length))group.open=true;});
  form.querySelectorAll('[data-production-person]').forEach(row=>{row.hidden=Boolean(field(row,'employee')?.value&&!personMatches(row,'people'));});
  form.querySelectorAll('[data-position-plan]').forEach(role=>{const people=[...role.querySelectorAll('[data-production-person]')];role.hidden=Boolean(Object.values(filters.people||{}).some(w=>w.values().length)&&!people.some(row=>!row.hidden&&field(row,'employee')?.value));});
  form.querySelectorAll('[data-position-group]').forEach(group=>{group.hidden=[...group.querySelectorAll('[data-position-plan]')].every(role=>role.hidden);});
  form.dispatchEvent(new Event('production-filter-change'));
 }
 form.productionFilters={workMatches,personMatches,syncGraph:()=>{}};
 form.querySelector('[data-work-search]')?.addEventListener('input',refresh);
 form.addEventListener('production-report-update',()=>{syncPeople();refresh();});form.addEventListener('production-live-applied',()=>{syncPeople();refresh();});form.addEventListener('change',event=>{if(event.target.closest('[data-production-person]')){syncPeople();refresh();}});
 syncPeople();refresh();
})();
