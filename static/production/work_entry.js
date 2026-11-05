/* Keep the dated work heading visible when adding another fact row. */
(() => {
  const source = document.getElementById('production-work-choices');
  const host = document.querySelector('[data-rows="works"]');
  if (!source || !host) return;
  const choices = JSON.parse(source.textContent);
  const catalogue = new Map(choices.map(choice => [choice.token, choice.display]));
  const itemOwners = new Map(choices.filter(choice => choice.item).map(choice => [String(choice.item), String(choice.work)]));
  const workMeta = new Map(choices.map(choice => [String(choice.work), choice]));
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:6});
  function refresh(row) {
    const work = row.querySelector('[name$="-project_work"]'), item = row.querySelector('[name$="-work_item"]');
    const location=row.querySelector('[name$="-complex_object_filter"]');
    if(location&&!location.dataset.workLocationReady){location.dataset.workLocationReady='1';location.addEventListener('change',()=>{work.value='';item.value='';work.dispatchEvent(new Event('change',{bubbles:true}));});}
    if(location&&work){for(const option of work.options){if(!option.value){option.hidden=false;option.disabled=false;continue;}const meta=workMeta.get(String(option.value));const available=location.value?meta?.kind==='COMPOSITE'&&String(meta.complex_id)===location.value:meta?.kind==='SIMPLE';option.hidden=!available;option.disabled=!available;if(!available&&option.selected)option.selected=false;}}
    if (work && item && item.tagName === 'SELECT') {
      const owner = String(work.value || '');
      const meta=workMeta.get(owner),simple=meta?.kind==='SIMPLE';
      for (const option of item.options) {
        if (!option.value) { option.hidden = false; option.disabled = false; continue; }
        const allowed = Boolean(owner) && itemOwners.get(String(option.value)) === owner;
        option.hidden = !allowed; option.disabled = !allowed;
        if (!allowed && option.selected) option.selected = false;
      }
      item.disabled=!owner||simple;if(simple)item.value='';
      const hint=row.querySelector('[data-work-item-hint]');if(hint)hint.textContent=simple?'Для самостоятельной работы это поле не заполняется.':owner?'Выберите работу в составе комплекса.':'Сначала выберите работу.';
    }
    const display = catalogue.get(item?.value ? `item:${work?.value}:${item.value}` : `work:${work?.value}`);
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
  function refreshAll(){host.querySelectorAll('[data-work-fact-row]').forEach(refresh);}
  function refreshGroupTotals(){
    host.querySelectorAll('[data-work-group-total]').forEach(total=>{const group=total.closest('[data-work-object]');let value=0;group.querySelectorAll('[data-work-fact-row]').forEach(row=>{const deleted=row.querySelector('[name$="-DELETE"]');const input=row.querySelector('[name$="-actual_quantity"]');if(!deleted?.checked&&input?.value.trim()){const parsed=Number(input.value.replace(',','.'));if(Number.isFinite(parsed))value+=parsed;}});total.querySelector('[data-work-group-actual]').textContent=number.format(value);});
  }
  refreshAll();refreshGroupTotals();host.closest('form').addEventListener('production-live-applied',()=>{refreshAll();refreshGroupTotals();});
  host.addEventListener('input',event=>{if(event.target.matches('[name$="-actual_quantity"]'))refreshGroupTotals();});
  host.addEventListener('change', event => {
    const row = event.target.closest('[data-work-fact-row]');
    if (row){refresh(row);refreshGroupTotals();}
  });
  host.addEventListener('click', event => {
    const expand = event.target.closest('[data-work-expand]'), collapse = event.target.closest('[data-work-collapse]');
    if (expand || collapse) host.querySelectorAll('[data-work-object], [data-work-package]').forEach(group => { group.open = Boolean(expand); });
  });
})();
