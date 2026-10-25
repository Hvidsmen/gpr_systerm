(()=>{
 const root=document.querySelector('[data-rows="equipment"]');if(!root)return;
 const update=(row,changed)=>{
  const get=name=>row.querySelector('[name$="-'+name+'"]');
  const state=get('state'),work=get('work'),item=get('work_item'),general=get('general'),activity=get('activity');
  if(!state||!activity)return;
  const working=state.value==='WORKING',previous=row._machineState;
  if(changed&&previous&&previous!==state.value){
   if(previous==='WORKING')row._workingValues={work:work?.value||'',item:item?.value||'',general:!!general?.checked,activity:activity.value};
   if(working){
    const saved=row._workingValues||{work:row.dataset.assignedWork||'',item:row.dataset.assignedItem||'',general:row.dataset.assignedGeneral==='1',activity:row.dataset.assignedActivity||''};
    if(work)work.value=saved.work;if(item)item.value=saved.item;if(general)general.checked=saved.general;activity.value=saved.activity;
   }else activity.value='';
  }
  for(const field of [work,item,general]){const container=field?.closest('.col-md-3');if(container)container.hidden=!working;}
  if(!working){if(work)work.value='';if(item)item.value='';if(general)general.checked=false;}
  const label=row.querySelector('label[for="'+activity.id+'"]');if(label)label.textContent=working?'Что делала / пояснение:':'Причина простоя или ремонта:';
  row._machineState=state.value;
 };
 const initialize=()=>root.querySelectorAll('[data-machine-fact]').forEach(row=>update(row,false));
 root.addEventListener('change',event=>{if(event.target===root){initialize();return;}if(event.target.name?.endsWith('-state')){const row=event.target.closest('[data-machine-fact]');if(row)update(row,true);}});
 initialize();
})();
