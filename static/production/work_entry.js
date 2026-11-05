/* Keep the dated work heading visible when adding another fact row. */
(() => {
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-work-expand], [data-work-collapse]');
    if (!button) return;
    const tree = button.closest('[data-rows="works"]');
    if (!tree) return;
    event.preventDefault();
    const expanded = button.hasAttribute('data-work-expand');
    tree.querySelectorAll('details[data-work-object], details[data-work-package]').forEach(group => { group.open = expanded; });
  });
  const source = document.getElementById('production-work-choices');
  const host = document.querySelector('[data-rows="works"]');
  if (!source || !host) return;
  const choices = JSON.parse(source.textContent);
  const catalogue = new Map(choices.map(choice => [choice.token, choice.display]));
  const itemOwners = new Map(choices.filter(choice => choice.item).map(choice => [String(choice.item), String(choice.work)]));
  const workMeta = new Map(JSON.parse(document.getElementById('production-work-meta')?.textContent||'[]').map(work=>[String(work.id),{kind:work.kind,complex_id:work.complex_object_id}]));
  choices.forEach(choice=>workMeta.set(String(choice.work),{...workMeta.get(String(choice.work)),...choice}));
  const creatorConfig=document.querySelector('[data-dispatcher-item-config]');
  const creatorObjects=new Set(JSON.parse(document.getElementById('dispatcher-item-object-ids')?.textContent||'[]').map(String));
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:6});
  function refresh(row) {
    const work = row.querySelector('[name$="-project_work"]'), item = row.querySelector('[name$="-work_item"]');
    const location=row.querySelector('[name$="-complex_object_filter"]');
    if(work&&!work._allWorkOptions)work._allWorkOptions=Array.from(work.options || []);
    if(location&&!location.dataset.workLocationReady){location.dataset.workLocationReady='1';location.addEventListener('change',()=>{work.value='';item.value='';filterWorks();work.dispatchEvent(new Event('change',{bubbles:true}));});}
    function filterWorks(){
      if(!location||!work)return;
      const selected=work.value;
      const allowed=work._allWorkOptions.filter(option=>!option.value||(location.value?workMeta.get(String(option.value))?.kind==='COMPOSITE'&&String(workMeta.get(String(option.value))?.complex_id)===location.value:workMeta.get(String(option.value))?.kind==='SIMPLE'));
      work.replaceChildren(...allowed);work.value=allowed.some(option=>option.value===selected)?selected:'';
    }
    filterWorks();
    if (work && item && item.tagName === 'SELECT') {
      const owner = String(work.value || '');
      const meta=workMeta.get(owner),simple=meta?.kind==='SIMPLE';
      for (const option of Array.from(item.options || [])) {
        if (!option.value) { option.hidden = false; option.disabled = false; continue; }
        const allowed = Boolean(owner) && itemOwners.get(String(option.value)) === owner;
        option.hidden = !allowed; option.disabled = !allowed;
        if (!allowed && option.selected) option.selected = false;
      }
      item.disabled=!owner||simple;if(simple)item.value='';
      const hint=row.querySelector('[data-work-item-hint]');if(hint)hint.textContent=simple?'Для самостоятельной работы это поле не заполняется.':owner?'Выберите работу в составе комплекса.':'Сначала выберите работу.';
      const creator=row.querySelector('[data-dispatcher-item-create]');if(creator)creator.hidden=!(owner&&meta?.kind==='COMPOSITE'&&creatorObjects.has(String(meta.complex_id)));
    }
    const selectedToken=item?.value ? `item:${work?.value}:${item.value}` : `work:${work?.value}`;
    const display = catalogue.get(selectedToken),selectedChoice=choices.find(choice=>choice.token===selectedToken);
    if(selectedChoice){row.dataset.workToken=selectedChoice.token;row.dataset.complexId=selectedChoice.complex_id||'';row.dataset.packageId=selectedChoice.work||'';}
    else if(work?.tagName==='SELECT'){row.dataset.workToken='';row.dataset.complexId='';row.dataset.packageId='';}
    const heading = row.querySelector('[data-work-heading]');
    if (!heading) return;
    if (!display) {
      if (work?.tagName === 'SELECT') heading.hidden = true;
      return;
    }
    heading.hidden = false;row.dataset.planned=display.has_plan?'1':'0';const remove=row.querySelector('[data-work-remove]');if(remove)remove.hidden=display.has_plan;
    row.querySelector('[data-work-title]').textContent = display.title;
    row.querySelector('[data-work-path]').textContent = display.path;
    row.querySelector('[data-work-unplanned]').hidden = display.has_plan;
    row.querySelector('[data-work-plan]').textContent = display.has_plan ? `${number.format(display.plan)} ${display.unit}` : '—';
    row.querySelector('[data-work-previous]').textContent = `${number.format(display.previous)} ${display.unit}`;
    const planned=row.querySelector('[data-work-plan-before]'),deviation=row.querySelector('[data-work-deviation]');
    if(planned)planned.textContent=display.plan_before==null?'—':`${number.format(display.plan_before)} ${display.unit}`;
    if(deviation){const value=display.deviation==null?null:Number(display.deviation);deviation.textContent=value==null?'—':`${value>0?'+':''}${number.format(value)}%`;deviation.classList.toggle('progress-behind',value!=null&&value<0);deviation.classList.toggle('progress-ahead',value!=null&&value>0);}

  }
  function setupCreator(row){
    const creator=row.querySelector('[data-dispatcher-item-create]');if(!creator||creator.dataset.ready)return;creator.dataset.ready='1';
    const fields=creator.querySelector('[data-dispatcher-item-fields]'),name=creator.querySelector('[data-dispatcher-item-name]'),unit=creator.querySelector('[data-dispatcher-item-unit]'),status=creator.querySelector('[data-dispatcher-item-status]');
    creator.querySelector('[data-dispatcher-item-toggle]').addEventListener('click',()=>{fields.hidden=false;name.focus();});
    creator.querySelector('[data-dispatcher-item-cancel]').addEventListener('click',()=>{fields.hidden=true;status.textContent='';});
    creator.querySelector('[data-dispatcher-item-submit]').addEventListener('click',async()=>{
      const work=row.querySelector('[name$="-project_work"]'),item=row.querySelector('[name$="-work_item"]'),form=row.closest('form');
      status.className='small';status.textContent='Создание…';
      const body=new FormData();body.set('construction_object',form.querySelector('[name=construction_object]').value);body.set('date',form.querySelector('[name=date]').value);body.set('project_work',work.value);body.set('name',name.value);body.set('unit',unit.value);
      try{
        const response=await fetch(creatorConfig.dataset.url,{method:'POST',headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body});const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось создать работу.');
        const choice=data.choice;choices.push(choice);catalogue.set(choice.token,choice.display);itemOwners.set(String(choice.item),String(choice.work));
        host.querySelectorAll('[name$="-work_item"]').forEach(select=>{if(!Array.from(select.options).some(option=>option.value===String(choice.item)))select.add(new Option(choice.label,choice.item));});
        item.value=String(choice.item);name.value='';unit.value='';fields.hidden=true;status.textContent='Работа создана и выбрана.';item.dispatchEvent(new Event('change',{bubbles:true}));
        form.dispatchEvent(new CustomEvent('production-work-created',{detail:choice}));
      }catch(error){status.className='small text-danger';status.textContent=error.message;}
    });
  }
  function refreshAll(){host.querySelectorAll('[data-work-fact-row]').forEach(row=>{setupCreator(row);refresh(row);});}
  function refreshGroupTotals(){
    host.querySelectorAll('[data-work-group-total]').forEach(total=>{const group=total.closest('[data-work-object]');let value=0;group.querySelectorAll('[data-work-fact-row]').forEach(row=>{const deleted=row.querySelector('[name$="-DELETE"]');const input=row.querySelector('[name$="-actual_quantity"]');if(!deleted?.checked&&input?.value.trim()){const parsed=Number(input.value.replace(',','.'));if(Number.isFinite(parsed))value+=parsed;}});total.querySelector('[data-work-group-actual]').textContent=number.format(value);});
  }
  refreshAll();refreshGroupTotals();host.closest('form').addEventListener('production-live-applied',()=>{refreshAll();refreshGroupTotals();});
  host.addEventListener('input',event=>{if(event.target.matches('[name$="-actual_quantity"]'))refreshGroupTotals();});
  host.addEventListener('change', event => {
    const row = event.target.closest('[data-work-fact-row]');
    if (row){refresh(row);refreshGroupTotals();}
    else if(event.target===host)refreshAll();
  });
  new MutationObserver(records=>{
    for(const record of records)for(const node of record.addedNodes){
      if(node.nodeType!==1)continue;
      if(node.matches?.('[data-work-fact-row]')){setupCreator(node);refresh(node);}
      node.querySelectorAll?.('[data-work-fact-row]').forEach(row=>{setupCreator(row);refresh(row);});
    }
  }).observe(host,{childList:true,subtree:true});
})();
