(()=>{
 'use strict';const source=document.getElementById('position-plan-saved');if(!source)return;
 let saved=JSON.parse(source.textContent);const staff=JSON.parse(document.getElementById('position-employee-roles').textContent),locations=JSON.parse(document.getElementById('production-employee-locations')?.textContent||'{}'),host=document.querySelector('[data-rows=labor]');
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),containers=new Map([...host.querySelectorAll('[data-position-people]')].map(el=>[el.dataset.positionPeople,el]));
 const number=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2});
 const positionStorageKey=`production-position-filter:${host.closest('form').elements.construction_object?.value||''}:${host.closest('form').elements.date?.value||''}`;let quickPosition=sessionStorage.getItem(positionStorageKey)||'all';
 let refreshPending=false;
 function scheduleRefresh(){if(refreshPending)return;refreshPending=true;requestAnimationFrame(()=>{refreshPending=false;refresh();});}
 function write(el,value){const text=number.format(value);if(el.textContent!==text)el.textContent=text;}
 function prioritizeEmployees(row,role){
  const select=field(row,'employee');if(!select)return;const current=select.value;select.querySelector('[data-position-matches]')?.remove();select.querySelectorAll('option[data-position-original]').forEach(option=>{option.hidden=false;delete option.dataset.positionOriginal;});
  if(!role)return;const matches=[...select.querySelectorAll('optgroup option')].filter(option=>option.value&&String(locations[option.value]?.position||'')===String(role));if(!matches.length)return;
  const label=row.closest('[data-position-plan]')?.querySelector('h4')?.textContent?.trim()||'выбранной должности',group=document.createElement('optgroup');group.label='Подходят по должности · '+label;group.dataset.positionMatches='1';
  matches.forEach(option=>{const clone=option.cloneNode(true);group.append(clone);option.hidden=true;option.dataset.positionOriginal='1';});const empty=[...select.children].find(node=>node.tagName==='OPTION');empty?.after(group);if(!empty)select.prepend(group);select.value=current;
 }
 const unplannedGroups=new Map(),unplannedPositions=new Map();
 function unplannedTarget(employee){
  const output=host.querySelector('[data-unplanned-groups]'),info=locations[employee]||{},group=info.staff_group||'Без группы',position=info.position_label||'Должность не указана';if(!output)return containers.get('');
  let details=unplannedGroups.get(group);if(!details){details=document.createElement('details');details.open=true;details.className='production-position-group';details.dataset.unplannedGroup=group;const summary=document.createElement('summary');summary.dataset.unplannedGroupLabel='';details.append(summary);output.append(details);unplannedGroups.set(group,details);}
  const key=group+'\u0000'+position;let target=unplannedPositions.get(key);if(!target){const block=document.createElement('section');block.className='border rounded p-3 mb-2';block.dataset.unplannedPosition=position;const title=document.createElement('h5');title.className='h6';title.dataset.unplannedPositionLabel='';target=document.createElement('div');target.dataset.unplannedRows='';block.append(title,target);details.append(block);unplannedPositions.set(key,target);}return target;
 }
 function updateUnplanned(){
  for(const details of unplannedGroups.values()){let total=0;details.querySelectorAll('[data-unplanned-rows]').forEach(target=>{const count=target.querySelectorAll(':scope > [data-production-person]').length,totalLabel=target.parentElement.querySelector('[data-unplanned-position-label]'),text=`${target.parentElement.dataset.unplannedPosition} · Людей: ${count}`;target.parentElement.hidden=count===0;total+=count;if(totalLabel&&totalLabel.textContent!==text)totalLabel.textContent=text;});details.hidden=total===0;const label=details.querySelector('[data-unplanned-group-label]'),text=`${details.dataset.unplannedGroup} · Людей: ${total}`;if(label&&label.textContent!==text)label.textContent=text;}
 }
 function refresh(){
  const records=new Map(saved.map(row=>[String(row.id),row])),extras=[];
  host.querySelectorAll('[data-production-person]').forEach(row=>{
   const id=field(row,'id')?.value,employee=field(row,'employee')?.value,role=field(row,'planning_position')?.value||row.dataset.requestedPosition||staff[employee],deleted=field(row,'DELETE')?.checked;
   if(id)records.delete(id);if(employee&&!deleted)extras.push({employee,role});
   const target=containers.get(String(role))||(employee?unplannedTarget(employee):containers.get(''));if(target&&row.parentElement!==target)target.append(row);prioritizeEmployees(row,role);
  });
  updateUnplanned();
  const people=new Map(),unique=new Set(),unplanned=new Set();
  for(const row of [...records.values(),...extras]){const key=String(row.role);if(!people.has(key))people.set(key,new Set());people.get(key).add(String(row.employee));unique.add(String(row.employee));if(!containers.has(key))unplanned.add(String(row.employee));}
  let plan=0,remaining=0;
  host.querySelectorAll('[data-position-plan]').forEach(row=>{const actual=people.get(row.dataset.positionPlan)?.size||0,required=Number(row.dataset.planCount),need=Math.max(0,required-actual);write(row.querySelector('[data-position-assigned]'),actual);write(row.querySelector('[data-position-remaining]'),need);row.dataset.positionActual=actual;row.dataset.positionRemaining=need;row.hidden=quickPosition==='deficit'?need===0:quickPosition==='assigned'?actual===0:quickPosition==='empty'?actual>0:false;plan+=required;remaining+=need;});
  host.querySelectorAll('[data-position-group]').forEach(group=>{let groupPlan=0,groupRemaining=0;const groupPeople=new Set();group.querySelectorAll('[data-position-plan]').forEach(role=>{const required=Number(role.dataset.planCount),assigned=people.get(role.dataset.positionPlan)||new Set();groupPlan+=required;groupRemaining+=Math.max(0,required-assigned.size);assigned.forEach(employee=>groupPeople.add(employee));});group.hidden=[...group.querySelectorAll('[data-position-plan]')].every(role=>role.hidden);write(group.querySelector('[data-position-group-plan]'),groupPlan);write(group.querySelector('[data-position-group-assigned]'),groupPeople.size);write(group.querySelector('[data-position-group-remaining]'),groupRemaining);});
  for(const [key,value] of Object.entries({plan,assigned:unique.size,remaining,unplanned:unplanned.size}))write(host.querySelector(`[data-position-total="${key}"]`),value);
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-add-for-position]');if(!button)return;
  let row=[...host.querySelectorAll('[data-production-person]')].find(row=>!field(row,'employee').value&&!field(row,'DELETE')?.checked&&!row.dataset.entryDirty&&(!row.dataset.requestedPosition||row.dataset.requestedPosition===button.dataset.addForPosition));
  if(!row){const before=new Set(host.querySelectorAll('[data-production-person]'));document.querySelector('[data-add=labor]').click();row=[...host.querySelectorAll('[data-production-person]')].find(row=>!before.has(row));}
  if(!row)return;row.dataset.requestedPosition=button.dataset.addForPosition;const role=field(row,'planning_position');role.value=button.dataset.addForPosition;prioritizeEmployees(row,button.dataset.addForPosition);if(!role.disabled)role.dispatchEvent(new Event('change',{bubbles:true}));refresh();row.scrollIntoView({block:'center'});(field(row,'employee').nextElementSibling?.querySelector('.search-select-button')||field(row,'employee')).focus();
 });
 document.addEventListener('click',event=>{const button=event.target.closest('[data-position-groups-toggle]');if(!button)return;host.querySelectorAll('[data-position-group],[data-unplanned-group]').forEach(group=>group.open=button.dataset.positionGroupsToggle==='open');});
 document.querySelectorAll('[data-position-quick]').forEach(button=>{const active=button.dataset.positionQuick===quickPosition;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
 document.addEventListener('click',event=>{const button=event.target.closest('[data-position-quick]');if(!button)return;quickPosition=button.dataset.positionQuick;sessionStorage.setItem(positionStorageKey,quickPosition);document.querySelectorAll('[data-position-quick]').forEach(candidate=>{const active=candidate===button;candidate.classList.toggle('active',active);candidate.setAttribute('aria-pressed',String(active));});scheduleRefresh();});
 host.addEventListener('change',event=>{const row=event.target.closest('[data-production-person]');if(row&&event.target===field(row,'planning_position'))delete row.dataset.requestedPosition;scheduleRefresh();});
 new MutationObserver(records=>{if(records.some(record=>[...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1&&(node.matches('[data-production-person]')||node.querySelector('[data-production-person]')))))scheduleRefresh();}).observe(host,{childList:true,subtree:true});
 host.closest('form').addEventListener('production-report-update',event=>{saved=event.detail.people.map(row=>({id:row.id,employee:row.employee,role:row.role}));scheduleRefresh();});
 const start=()=>refresh();
 if(window.productionPerformance)host.closest('form').addEventListener('production-background-ready',start,{once:true});else start();
})();
