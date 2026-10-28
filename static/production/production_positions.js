(()=>{
 'use strict';const source=document.getElementById('position-plan-saved');if(!source)return;
 let saved=JSON.parse(source.textContent);const staff=JSON.parse(document.getElementById('position-employee-roles').textContent),host=document.querySelector('[data-rows=labor]');
 const field=(row,name)=>row.querySelector(`[name$="-${name}"]`),containers=new Map([...host.querySelectorAll('[data-position-people]')].map(el=>[el.dataset.positionPeople,el]));
 const number=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2});
 function write(el,value){const text=number.format(value);if(el.textContent!==text)el.textContent=text;}
 function refresh(){
  const records=new Map(saved.map(row=>[String(row.id),row])),extras=[];
  host.querySelectorAll('[data-production-person]').forEach(row=>{
   const id=field(row,'id')?.value,employee=field(row,'employee')?.value,role=field(row,'planning_position')?.value||row.dataset.requestedPosition||staff[employee],deleted=field(row,'DELETE')?.checked;
   if(id)records.delete(id);if(employee&&!deleted)extras.push({employee,role});
   const target=containers.get(String(role))||containers.get('');if(target&&row.parentElement!==target)target.append(row);
  });
  const people=new Map(),unique=new Set(),unplanned=new Set();
  for(const row of [...records.values(),...extras]){const key=String(row.role);if(!people.has(key))people.set(key,new Set());people.get(key).add(String(row.employee));unique.add(String(row.employee));if(!containers.has(key))unplanned.add(String(row.employee));}
  let plan=0,remaining=0;
  host.querySelectorAll('[data-position-plan]').forEach(row=>{const actual=people.get(row.dataset.positionPlan)?.size||0,required=Number(row.dataset.planCount),need=Math.max(0,required-actual);write(row.querySelector('[data-position-assigned]'),actual);write(row.querySelector('[data-position-remaining]'),need);plan+=required;remaining+=need;});
  for(const [key,value] of Object.entries({plan,assigned:unique.size,remaining,unplanned:unplanned.size}))write(host.querySelector(`[data-position-total="${key}"]`),value);
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-add-for-position]');if(!button)return;
  let row=[...host.querySelectorAll('[data-production-person]')].find(row=>!field(row,'employee').value&&!field(row,'DELETE')?.checked&&!row.dataset.entryDirty&&(!row.dataset.requestedPosition||row.dataset.requestedPosition===button.dataset.addForPosition));
  if(!row){const before=new Set(host.querySelectorAll('[data-production-person]'));document.querySelector('[data-add=labor]').click();row=[...host.querySelectorAll('[data-production-person]')].find(row=>!before.has(row));}
  if(!row)return;row.dataset.requestedPosition=button.dataset.addForPosition;const role=field(row,'planning_position');role.value=button.dataset.addForPosition;if(!role.disabled)role.dispatchEvent(new Event('change',{bubbles:true}));refresh();row.scrollIntoView({block:'center'});field(row,'employee').focus();
 });
 host.addEventListener('change',event=>{const row=event.target.closest('[data-production-person]');if(row&&event.target===field(row,'planning_position'))delete row.dataset.requestedPosition;refresh();});
 new MutationObserver(records=>{if(records.some(record=>[...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1&&(node.matches('[data-production-person]')||node.querySelector('[data-production-person]')))))refresh();}).observe(host,{childList:true,subtree:true});
 host.closest('form').addEventListener('production-report-update',event=>{saved=event.detail.people.map(row=>({id:row.id,employee:row.employee,role:row.role}));refresh();});
 refresh();
})();
