(() => {
  const source=document.getElementById('resource-editor-data');
  if(!source)return;
  const data=JSON.parse(source.textContent), dialog=document.getElementById('work-resource-dialog');
  const form=document.getElementById('work-resource-form');
  const kinds={equipment:'Техника',labor:'Должность',fuel:'ГСМ'};
  const format=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3});
  const field=name=>form?.elements.namedItem(name);
  const targetById=id=>data.targets.find(t=>String(t.id)===String(id));
  const error=message=>{const box=document.getElementById('work-resource-errors');box.textContent=message;box.hidden=!message;};
  function addButtons(container,work='',item='') {
    if(!data.can_edit)return;
    const bar=document.createElement('div');bar.className='d-flex flex-wrap gap-1 mt-2';bar.dataset.resourceActions='';
    Object.entries(kinds).forEach(([kind,label])=>{const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-outline-primary';button.textContent=label;button.dataset.addResource=kind;button.dataset.work=work;button.dataset.item=item;bar.append(button);});
    container.append(bar);return bar;
  }
  function wireWork(row) {
    row.querySelector('[data-parent-resource-actions]')?.remove();
    const work=row.querySelector('[name$="-work"]');if(!work?.value)return;
    const bar=addButtons(row.querySelector('.allocation-grid'),work.value);
    if(bar){bar.dataset.parentResourceActions='';bar.style.gridColumn='1 / -1';}
  }
  function open(kind,work='',item='',assignment=null) {
    if(!form)return;
    const target=targetById(work),child=target?.items.find(i=>String(i.id)===String(item));
    if(!assignment && work && (!target || item && !child)){alert('Сначала сохраните эту работу в месячном плане. Затем добавьте ресурсы.');return;}
    form.reset();error('');
    const initial={assignment_id:'',resource_kind:kind,work,work_item:item,start_date:data.start_date,end_date:data.end_date,count:1,shifts_per_day:1,hours_per_shift:8,daily_liters:0,fuel_price:0,include_crew:true,refresh_crew:false};
    if(assignment)Object.assign(initial,assignment,{assignment_id:assignment.id});
    Object.entries(initial).forEach(([name,value])=>{const input=field(name);if(!input)return;if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value;});
    document.getElementById('work-resource-title').textContent=(assignment?'Изменить ресурс: ':'Добавить ресурс: ')+kinds[kind];
    document.getElementById('work-resource-target').textContent=work?'К работе: '+(child?target.name+' → '+child.name:(target?.name||assignment?.target||'Работа удалена из плана')):'Вне работ · общие ресурсы объекта';
    form.querySelectorAll('[data-resource-field]').forEach(box=>{const visible=box.dataset.resourceField===kind || box.dataset.resourceField==='quantity' && kind!=='fuel';box.hidden=!visible;box.querySelectorAll('input,select').forEach(input=>input.disabled=!visible);});
    field('refresh_crew').closest('p').hidden=!assignment || kind!=='equipment';
    form.querySelector('[data-delete-resource]').hidden=!assignment;
    dialog.showModal();
  }
  function button(label,callback,danger=false){const b=document.createElement('button');b.type='button';b.textContent=label;b.className='btn btn-sm '+(danger?'btn-outline-danger':'btn-outline-primary');b.addEventListener('click',callback);return b;}
  function card(assignment,projection=null) {
    const box=document.createElement('div');box.className='border rounded p-3 mb-2';
    const title=document.createElement('strong');title.textContent=projection?.label||assignment.label;
    const badge=document.createElement('span');badge.className='badge bg-primary ms-2';badge.textContent=assignment.work?'Из работы: '+assignment.target:'Вне работ';
    const detail=document.createElement('p');detail.className='small mb-2 mt-2';
    const resourceKind=projection?.kind||assignment.resource_kind;
    detail.textContent=resourceKind==='fuel' ? format.format(Number(projection?.liters||assignment.daily_liters))+' л/день' : format.format(projection?.count??assignment.count)+(resourceKind==='equipment'?' ед.':' чел. на смену')+' · '+assignment.shifts_per_day+' смен/день · '+assignment.hours_per_shift+' ч/смену';
    detail.append(document.createTextNode(' · '+assignment.start_date+' — '+assignment.end_date));
    box.append(title,badge,detail);
    if(assignment.crew && !projection){const crew=document.createElement('p');crew.className='small';crew.textContent='Экипаж на единицу: '+assignment.crew;box.append(crew);}
    if(data.can_edit)box.append(button('Изменить',()=>open(assignment.resource_kind,assignment.work,assignment.work_item,assignment)));
    return box;
  }
  function hiddenInput(row,name,value,type='hidden') {const input=document.createElement('input');input.type=type;input.name='generated-'+name;input.value=value;input.disabled=true;input.hidden=true;row.append(input);}
  function render() {
    const start=data.start_date,end=data.end_date;
    const active=data.assignments.filter(a=>a.end_date>=start&&a.start_date<=end);
    document.querySelectorAll('[data-resource-assignment-list]').forEach(list=>{list.replaceChildren();if(!active.length)list.textContent='Назначений пока нет.';active.forEach(a=>list.append(card(a)));});
    document.querySelectorAll('[data-generated-resource]').forEach(row=>row.remove());
    active.forEach(a=>(a.projected||[]).forEach(projection=>{
      const target=document.getElementById('resource-rows-'+projection.kind);if(!target)return;
      const row=card(a,projection);row.classList.add('allocation-row');row.dataset.generatedResource=String(a.id);
      // Disabled selectors integrate the calculated rows with existing filters and counters.
      // They are display-only and never enter the monthly allocation formset.
      const identity={equipment:'equipment_type',labor:'brigade',fuel:'fuel_type'}[projection.kind];
      const select=document.createElement('select');select.name='generated-'+identity;select.disabled=true;select.hidden=true;select.dataset.nativeSelect='true';
      select.add(new Option(projection.label,String(projection.identity),true,true));row.append(select);
      hiddenInput(row,'DELETE','', 'checkbox');
      const day=value=>Date.parse(value+'T00:00:00Z')/86400000;
      const activeDays=Math.max(0,day(a.end_date<end?a.end_date:end)-day(a.start_date>start?a.start_date:start)+1);
      const days=day(end)-day(start)+1;
      hiddenInput(row,'count',Number(projection.count||0)*activeDays/days);
      target.append(row);
    }));
    document.dispatchEvent(new CustomEvent('gpr-resource-rendered'));
    window.dispatchEvent(new CustomEvent('gpr-resources-changed'));
  }
  document.addEventListener('click',event=>{
    const add=event.target.closest('[data-add-resource]');if(add)open(add.dataset.addResource,add.dataset.work,add.dataset.item);
    if(event.target.closest('[data-close-resource]'))dialog.close();
  });
  let busy=false;
  async function submit(remove=false) {
    if(busy)return;
    if(remove && !confirm('Удалить это назначение ресурса?'))return;
    if(!remove && !form.reportValidity())return;
    busy=true;error('');form.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const payload=new FormData(form);if(remove)payload.set('action','delete');
      const response=await fetch(form.action,{method:'POST',body:payload,headers:{Accept:'application/json'},credentials:'same-origin'});
      const contentType=response.headers.get('Content-Type')||'';
      if(!contentType.includes('application/json'))throw new Error(response.status===403?'Нет права изменять ресурсы этого плана. Обновите страницу.':'Не удалось сохранить ресурс. Проверьте вход в портал и повторите попытку.');
      const result=await response.json();
      if(!response.ok)throw new Error(Object.values(result.errors||{}).flat().map(e=>e.message).join(' ')||'Не удалось сохранить ресурс.');
      if(result.deleted)data.assignments=data.assignments.filter(a=>a.id!==result.deleted);
      else {const index=data.assignments.findIndex(a=>a.id===result.assignment.id);if(index<0)data.assignments.push(result.assignment);else data.assignments[index]=result.assignment;}
      render();dialog.close();
    }catch(e){error(e.message);}finally{busy=false;form.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  form?.addEventListener('submit',event=>{event.preventDefault();submit();});
  form?.querySelector('[data-delete-resource]').addEventListener('click',()=>submit(true));
  window.gprResourceEditor={wireWork,addButtons};
  if(window.wireSubworks)window.wireSubworks();
  // Common resource actions sit next to the monthly resource sections.
  const heading=document.querySelector('#allocation-editor h3');
  if(heading){const common=document.createElement('div');common.className='mb-3';const label=document.createElement('strong');label.textContent='Добавить ресурс вне работ';common.append(label);addButtons(common);heading.after(common);}
  render();
})();
