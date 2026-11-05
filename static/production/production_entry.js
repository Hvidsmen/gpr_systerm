(() => {
 const source=document.getElementById('production-work-choices'),dialog=document.querySelector('[data-person-work-dialog]');if(!source||!dialog)return;
 const employeeLocations=JSON.parse(document.getElementById('production-employee-locations')?.textContent||'{}');
 const choices=JSON.parse(source.textContent),labels=new Map(choices.map(row=>[row.token,row.label]));let owner=null,original=[];
 const token=(work,item)=>item?'item:'+work+':'+item:'work:'+work,factToken=f=>f.operational_name?'operational:'+f.id:token(f.project_work_id,f.work_item_id);
 function refresh(row){
  const employee=row.querySelector('[name$="-employee"]'),hint=row.querySelector('[data-employee-location]'),location=employeeLocations[employee?.value];if(hint){hint.textContent=location?location.text+(location.group===2?' · Факт доступен. После сохранения работы сведения попадут в сверку.':''):'';hint.classList.toggle('text-warning-emphasis',Boolean(location&&location.group===2));hint.classList.toggle('text-muted',!location||location.group!==2);}
  const mode=row.querySelector('[name$="-work_mode"]'),box=row.querySelector('[data-person-targets]');if(!mode||!box)return;box.hidden=true;
  const list=row.querySelector('[data-person-target-labels]');list.replaceChildren();
  const selected=[...row.querySelectorAll('[name$="-work_targets"]')].map(input=>input.value);
  for(const value of selected){const li=document.createElement('li');li.textContent=labels.get(value)||value;list.append(li);}
  if(!selected.length){const li=document.createElement('li');li.textContent='Работы пока не выбраны';list.append(li);}
 }
 function available(){const saved=JSON.parse(document.getElementById('production-graph-saved')?.textContent||'[]'),result=new Set(saved.filter(r=>Number(r.actual_quantity)>0).map(factToken));document.querySelectorAll('[data-work-fact-row]').forEach(row=>{
   const work=row.querySelector('[name$="-project_work"]'),item=row.querySelector('[name$="-work_item"]'),name=row.querySelector('[name$="-operational_name"]'),deleted=row.querySelector('[name$="-DELETE"]');
   if(work?.value){const key=name?.value.trim()&&row.dataset.factId?'operational:'+row.dataset.factId:token(work.value,item?.value);if(!deleted?.checked&&Number(row.querySelector('[name$="-actual_quantity"]')?.value.replace(',','.'))>0)result.add(key);else result.delete(key);}
  });return result;}
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-person-pick]');if(!button)return;owner=button.closest('[data-production-person]');
  const selected=new Set([...owner.querySelectorAll('[name$="-work_targets"]')].map(input=>input.value));original=[...selected];const allowed=available(),host=dialog.querySelector('[data-person-work-options]');host.replaceChildren();
  const saved=JSON.parse(document.getElementById('production-graph-saved')?.textContent||'[]'),operational=saved.filter(f=>f.operational_name).map(f=>({token:factToken(f),label:'Прочее → '+f.operational_name}));for(const choice of [...choices,...operational]){labels.set(choice.token,choice.label);if(!allowed.has(choice.token)&&!selected.has(choice.token))continue;
   const label=document.createElement('label');label.className='production-work-option';label.dataset.search=choice.label.toLocaleLowerCase('ru');
   const input=document.createElement('input');input.type='checkbox';input.value=choice.token;input.checked=selected.has(choice.token);label.append(input,document.createTextNode(choice.label));host.append(label);
  }
  if(!host.children.length){const p=document.createElement('p');p.className='small text-muted';p.textContent='Сначала добавьте работу в список факта за этот день.';host.append(p);}
  dialog.querySelector('[data-person-work-search]').value='';dialog.showModal();
 });
 dialog.querySelector('[data-person-work-search]').addEventListener('input',event=>{const terms=event.target.value.toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);dialog.querySelectorAll('[data-search]').forEach(row=>{row.hidden=!terms.every(term=>row.dataset.search.includes(term));});});
 dialog.querySelector('[data-person-work-close]').addEventListener('click',()=>dialog.close());
 dialog.querySelector('[data-person-work-apply]').addEventListener('click',async()=>{
  const selected=[...dialog.querySelectorAll('input[type=checkbox]:checked')].map(input=>input.value),saved=JSON.parse(document.getElementById('production-graph-saved').textContent);
  const edges=[];for(const target of new Set([...original,...selected])){
   if(original.includes(target)===selected.includes(target))continue;
   const fact=saved.find(w=>factToken(w)===target);if(!fact)continue;
   edges.push({action:selected.includes(target)?'add':'remove',fact:fact.id});
  }
  try{await owner.closest('form').productionChangeLinks(owner,{action:'delta',edges});refresh(owner);dialog.close();}
  catch(error){dialog.querySelector('[data-person-work-options]').prepend(document.createTextNode(error.message+' '));}
 });
 document.addEventListener('change',event=>{const row=event.target.closest('[data-production-person]');if(row)refresh(row);});
 new MutationObserver(()=>document.querySelectorAll('[data-production-person]').forEach(refresh)).observe(document.querySelector('[data-rows="labor"]')||document.body,{childList:true});
 document.querySelectorAll('[data-production-person]').forEach(refresh);
})();
