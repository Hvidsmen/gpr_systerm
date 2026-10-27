(() => {
  const form=document.querySelector('[data-machine-form]'),filters=document.querySelector('[data-machine-filters]');
  function modelsFor(host,id,sourceId,list){
    const source=document.getElementById(sourceId);if(!host||!source)return;
    const parent=host.querySelector('[name="'+id+'"]'),model=host.querySelector('[name=model]'),rows=JSON.parse(source.textContent);
    if(!parent||!model)return;
    function populate(keep=true){
      const selected=keep?model.value:'';
      model.replaceChildren(new Option(list?'Все модели':'Без отдельной модели',''));
      for(const row of rows){if(parent.value&&String(row.parent_id)!==parent.value)continue;if(!list&&!parent.value)continue;
        const label=list?(row['parent__name']+' · '):'';model.add(new Option(label+(row.brand_model||row.name),String(row.id)));
      }
      if(Array.from(model.options).some(option=>option.value===selected))model.value=selected;
    }
    parent.addEventListener('change',()=>{populate(false);model.dispatchEvent(new Event('change',{bubbles:true}));});
    populate();
    if(!list){
      const name=host.querySelector('[name=name]'),brand=host.querySelector('[name=brand_model]');let lastName=name.value;
      model.addEventListener('change',()=>{const row=rows.find(item=>String(item.id)===model.value);if(!row)return;
        if(!brand.value)brand.value=row.brand_model||row.name;
        if(!name.value||name.value===lastName){name.value=row.brand_model||row.name;lastName=name.value;}
      });
    }
  }
  modelsFor(form,'type','machine-form-models',false);modelsFor(filters,'parent','machine-filter-models',true);
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-copy-value]');if(!button)return;
    let copied=false;try{await navigator.clipboard.writeText(button.dataset.copyValue);copied=true;}catch(error){
      const field=document.createElement('textarea');field.value=button.dataset.copyValue;field.style.cssText='position:fixed;left:-9999px';document.body.append(field);field.select();copied=document.execCommand('copy');field.remove();button.focus();
    }
    const status=document.querySelector('[data-machine-status]');if(status)status.textContent=copied?'Номер скопирован':'Не удалось скопировать номер';
    if(copied){const label=button.textContent;button.textContent='Скопировано';setTimeout(()=>{button.textContent=label;},1500);}
  });
})();
