(()=>{
 const data=document.getElementById('access-group-options');if(!data)return;
 const config=JSON.parse(data.textContent),group=document.querySelector('[name=access_group]'),role=document.querySelector('[name=role]'),objects=document.querySelector('[name=objects]'),hint=document.querySelector('[data-access-scope]');
 if(!group||!role||!objects)return;
 function explain(){const code=config.roles[role.value],limited=['PLANNER','FOREMAN','DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER'].includes(code);hint.textContent=limited?'Доступ только к назначенным объектам. Если список пуст, доступа к объектам нет.':code?'Эта роль имеет доступ ко всем объектам своей компании. Выбранный список объектов не ограничивает её доступ.':'Выберите группу или роль, чтобы увидеть область доступа.';}
 function changed(){const selected=config.groups.find(item=>String(item.id)===group.value);if(selected){role.value=String(selected.role);for(const option of objects.options)option.selected=selected.objects.map(String).includes(option.value);}role.disabled=Boolean(selected);objects.disabled=Boolean(selected);for(const field of [role,objects])field.dispatchEvent(new Event('change',{bubbles:true}));explain();}
 group.addEventListener('change',changed);role.addEventListener('change',explain);changed();
})();
