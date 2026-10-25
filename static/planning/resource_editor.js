(() => {
  const source=document.getElementById('resource-editor-data');
  if(!source)return;
  const data=JSON.parse(source.textContent),form=document.getElementById('work-resource-form');
  const kinds={equipment:'Техника',labor:'Должность',fuel:'ГСМ'};
  const format=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3});
  const field=name=>form?.elements.namedItem(name);
  const targetById=id=>data.targets.find(t=>String(t.id)===String(id));
  let editor=null,editorTarget=null,dirty=false,busy=false;
  const foldStates=new Map();
  const foldKey=host=>String(host.dataset.resourceWork||'')+'/'+String(host.dataset.resourceItem||'');
  document.addEventListener('toggle',event=>{const fold=event.target;if(fold.matches?.('[data-resource-fold]')&&fold.isConnected){const host=fold.closest('[data-work-resource-target]');if(host)foldStates.set(foldKey(host),fold.open);}},true);
  const activeAssignments=()=>data.assignments.filter(a=>a.end_date>=data.start_date&&a.start_date<=data.end_date);
  const sameTarget=(assignment,work,item)=>String(assignment.work||'')===String(work||'')&&String(assignment.work_item||'')===String(item||'');
  function error(message) {
    const box=editor?.querySelector('[role="alert"]');
    if(box){box.textContent=message;box.hidden=!message;}
  }
  function addButtons(container,work='',item='') {
    const host=document.createElement('div');host.dataset.workResourceTarget='';host.dataset.resourceWork=work;host.dataset.resourceItem=item;
    const fold=document.createElement('details');fold.dataset.resourceFold='';fold.open=foldStates.get(foldKey(host))??true;
    const summary=document.createElement('summary');summary.className='work-resource-summary';summary.textContent='Ресурсы';
    const list=document.createElement('div');list.dataset.workResourceList='';fold.append(summary,list);
    host.append(fold);container.append(host);renderHost(host);return host;
  }
  function wireWork(row) {
    const work=row.querySelector('[name$="-work"]');
    let host=row.querySelector('[data-parent-resource-actions]');
    if(!work?.value){host?.remove();return;}
    if(host && host.dataset.resourceWork===work.value)return;
    host?.remove();host=addButtons(row.querySelector('.allocation-grid'),work.value);
    host.dataset.parentResourceActions='';host.style.gridColumn='1 / -1';
  }
  function closeEditor() {if(busy)return;editor?.remove();editor=null;editorTarget=null;dirty=false;}
  function mountEditor(anchor) {
    if(anchor?.isConnected){anchor.closest('[data-resource-fold]')?.setAttribute('open','');anchor.append(editor);return;}
    const target=Array.from(document.querySelectorAll('[data-work-resource-target]')).find(h=>sameTarget({work:h.dataset.resourceWork,work_item:h.dataset.resourceItem},editorTarget?.work,editorTarget?.item));
    const destination=target?.querySelector('[data-resource-inline-slot]')||document.getElementById('work-resource-editor-fallback');
    target?.querySelector('[data-resource-fold]')?.setAttribute('open','');
    destination.hidden=false;destination.append(editor);
  }
  function open(kind,work='',item='',assignment=null,anchor=null) {
    if(!form||busy)return;
    if(editor&&dirty){error('Сначала сохраните или отмените изменения этого ресурса.');editor.scrollIntoView({block:'nearest'});return;}
    closeEditor();
    editor=document.getElementById('work-resource-editor-template').content.firstElementChild.cloneNode(true);
    editorTarget={work,item,kind};
    // Controls belong to the external resource form even when shown inside the monthly editor.
    // This keeps resource changes separate from unsaved work volumes, without nested forms.
    editor.querySelectorAll('input,select,textarea,button').forEach(input=>input.setAttribute('form',form.id));
    mountEditor(anchor);
    const target=targetById(work),child=target?.items.find(i=>String(i.id)===String(item));
    const initial={assignment_id:'',resource_kind:kind,work,work_item:item,start_date:data.start_date,end_date:data.end_date,count:1,shifts_per_day:1,hours_per_shift:8,daily_liters:0,fuel_price:0,include_crew:true,refresh_crew:false};
    if(assignment)Object.assign(initial,assignment,{assignment_id:assignment.id});
    Object.entries(initial).forEach(([name,value])=>{const input=field(name);if(!input)return;if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value;});
    editor.querySelector('#work-resource-title').textContent=(assignment?'Изменить ресурс: ':'Добавить ресурс: ')+kinds[kind];
    editor.querySelector('#work-resource-target').textContent=work?'К работе: '+(child?target.name+' → '+child.name:(target?.name||assignment?.target||'Работа удалена из плана')):'Вне работ · общие ресурсы объекта';
    editor.querySelectorAll('[data-resource-field]').forEach(box=>{const visible=box.dataset.resourceField===kind||box.dataset.resourceField==='quantity'&&kind!=='fuel';box.hidden=!visible;box.querySelectorAll('input,select').forEach(input=>input.disabled=!visible);});
    field('refresh_crew').closest('p').hidden=!assignment||kind!=='equipment';
    editor.querySelector('[data-delete-resource]').hidden=!assignment;
    if(!assignment&&work&&(!target||item&&!child))error('Сначала сохраните эту работу в месячном плане. Затем добавьте ресурсы.');
    editor.addEventListener('input',()=>{dirty=true;});
    editor.addEventListener('change',()=>{dirty=true;});
    editor.scrollIntoView({block:'nearest'});
  }
  function button(label,callback){const b=document.createElement('button');b.type='button';b.textContent=label;b.className='btn btn-sm btn-outline-primary';b.addEventListener('click',callback);return b;}
  function card(assignment,projection=null,compact=false) {
    const box=document.createElement('div');box.className=compact?'work-resource-entry':'border rounded p-3 mb-2 bg-white';box.dataset.resourceCard=String(assignment.id);box.dataset.resourceKind=assignment.resource_kind;
    const title=document.createElement('strong');title.textContent=projection?.label||assignment.label;
    const badge=document.createElement('span');badge.className='badge bg-primary ms-2';badge.textContent=assignment.work?'Из работы: '+assignment.target:'Вне работ';
    const detail=document.createElement('p');detail.className='small mb-2 mt-2';
    const kind=projection?.kind||assignment.resource_kind;
    detail.textContent=kind==='fuel'?format.format(Number(projection?.liters||assignment.daily_liters))+' л/день':format.format(projection?.count??assignment.count)+(kind==='equipment'?' ед.':projection?' чел. за день':' чел. на смену')+' · '+assignment.shifts_per_day+' смен/день · '+assignment.hours_per_shift+' ч/смену';
    const shortDate=value=>value.split('-').reverse().join('.');
    detail.append(document.createTextNode(' · '+shortDate(assignment.start_date)+' — '+shortDate(assignment.end_date)));box.append(title);if(!compact)box.append(badge);box.append(detail);
    if(assignment.resource_kind==='equipment'&&assignment.crew&&!projection){
      const crew=document.createElement('div');crew.className='small mb-2 work-resource-crew';crew.dataset.resourceCrew='';
      const label=document.createElement('strong');label.textContent='Экипаж техники';crew.append(label);
      (assignment.projected||[]).filter(p=>p.kind==='labor').forEach(p=>{const line=document.createElement('div');line.textContent=p.label+' — '+format.format(p.count)+' чел. за день';crew.append(line);});
      box.append(crew);
    }
    if(data.can_edit){
      const edit=button('Изменить',()=>open(assignment.resource_kind,assignment.work,assignment.work_item,assignment,compact?box.closest('[data-work-resource-target]')?.querySelector('[data-resource-inline-slot]'):box));
      if(compact){const heading=document.createElement('div');heading.className='work-resource-entry-heading';box.insertBefore(heading,title);heading.append(title,edit);}else box.append(edit);
    }
    return box;
  }
  function renderHost(host) {
    let list=host.querySelector('[data-work-resource-list]');if(!list)return;
    host.querySelector('[data-resource-actions]')?.remove();
    if(!host.querySelector('[data-resource-fold]')){
      const fold=document.createElement('details');fold.dataset.resourceFold='';fold.open=foldStates.get(foldKey(host))??true;
      const summary=document.createElement('summary');summary.className='work-resource-summary';fold.append(summary);host.insertBefore(fold,list);fold.append(list);
    }
    const assignments=activeAssignments().filter(a=>sameTarget(a,host.dataset.resourceWork,host.dataset.resourceItem));
    host.querySelector('.work-resource-summary').textContent='Ресурсы · '+Object.entries(kinds).map(([kind,label])=>label+': '+assignments.filter(a=>a.resource_kind===kind).length).join(' · ');
    const scroll=document.createElement('div');scroll.className='work-resource-columns';
    const table=document.createElement('table');table.className='work-resource-table';table.dataset.resourceColumns='';
    const headings=table.createTHead().insertRow(),row=table.createTBody().insertRow();
    Object.entries(kinds).forEach(([kind,label])=>{
      const heading=document.createElement('th');heading.scope='col';heading.textContent=kind==='equipment'?'Техника и экипаж':kind==='labor'?'Должности':label;headings.append(heading);
      const cell=row.insertCell();cell.dataset.resourceColumn=kind;
      if(data.can_edit){const add=document.createElement('button');add.type='button';add.className='btn btn-sm btn-outline-primary work-resource-add';add.textContent='Добавить '+(kind==='equipment'?'технику':kind==='labor'?'должность':'ГСМ');add.dataset.addResource=kind;add.dataset.work=host.dataset.resourceWork;add.dataset.item=host.dataset.resourceItem;heading.append(add);}
      const selected=assignments.filter(a=>a.resource_kind===kind);
      selected.forEach(a=>cell.append(card(a,null,true)));
      if(!selected.length){const empty=document.createElement('div');empty.className='work-resource-empty';empty.textContent='—';cell.append(empty);}
    });
    scroll.append(table);
    const slot=document.createElement('div');slot.dataset.resourceInlineSlot='';
    list.replaceChildren(scroll,slot);list.hidden=false;
  }

  function renderWorkLists() {
    document.querySelectorAll('[data-work-resource-target]').forEach(renderHost);
    if(editor&&!editor.isConnected)mountEditor();
  }
  function hiddenInput(row,name,value,type='hidden') {const input=document.createElement('input');input.type=type;input.name='generated-'+name;input.value=value;input.disabled=true;input.hidden=true;row.append(input);}
  function render() {
    const start=data.start_date,end=data.end_date,active=activeAssignments();
    document.querySelectorAll('[data-resource-assignment-list]').forEach(list=>{list.replaceChildren();if(!active.length)list.textContent='Назначений пока нет.';active.forEach(a=>list.append(card(a)));});
    document.querySelectorAll('[data-generated-resource]').forEach(row=>row.remove());
    active.forEach(a=>(a.projected||[]).forEach(projection=>{
      const target=document.getElementById('resource-rows-'+projection.kind);if(!target)return;
      const row=card(a,projection);row.classList.add('allocation-row');row.dataset.generatedResource=String(a.id);
      const identity={equipment:'equipment_type',labor:'brigade',fuel:'fuel_type'}[projection.kind];
      const select=document.createElement('select');select.name='generated-'+identity;select.disabled=true;select.hidden=true;select.dataset.nativeSelect='true';select.add(new Option(projection.label,String(projection.identity),true,true));row.append(select);
      hiddenInput(row,'DELETE','','checkbox');
      const day=value=>Date.parse(value+'T00:00:00Z')/86400000;
      const activeDays=Math.max(0,day(a.end_date<end?a.end_date:end)-day(a.start_date>start?a.start_date:start)+1);
      hiddenInput(row,'count',Number(projection.count||0)*activeDays/(day(end)-day(start)+1));target.append(row);
    }));
    renderWorkLists();
    document.dispatchEvent(new CustomEvent('gpr-resource-rendered'));
    window.dispatchEvent(new CustomEvent('gpr-resources-changed'));
  }
  document.addEventListener('click',event=>{
    const add=event.target.closest('[data-add-resource]');
    if(add)open(add.dataset.addResource,add.dataset.work,add.dataset.item,null,add.closest('[data-work-resource-target]')?.querySelector('[data-resource-inline-slot]')||add.parentElement);
    if(event.target.closest('[data-close-resource]'))closeEditor();
    if(event.target.closest('[data-delete-resource]')){
      const confirmButton=editor.querySelector('[data-confirm-delete-resource]');confirmButton.hidden=false;
      error('Подтвердите удаление назначения. Оно исчезнет из работы и общих ресурсов.');
    }
    if(event.target.closest('[data-confirm-delete-resource]'))submit(true);
    const toggle=event.target.closest('[data-resource-expand]');
    if(toggle)document.querySelectorAll('#work-resources [data-resource-work-group],#work-resources [data-resource-fold]').forEach(block=>block.open=toggle.dataset.resourceExpand==='true');
  });
  async function submit(remove=false) {
    if(busy||!editor)return;
    if(!remove&&!form.reportValidity())return;
    busy=true;error('');const currentEditor=editor;currentEditor.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const payload=new FormData(form);if(remove)payload.set('action','delete');
      const response=await fetch(form.action,{method:'POST',body:payload,headers:{Accept:'application/json'},credentials:'same-origin'});
      if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw new Error(response.status===403?'Нет права изменять ресурсы этого плана. Обновите страницу.':'Не удалось сохранить ресурс. Проверьте вход в портал и повторите попытку.');
      const result=await response.json();
      if(!response.ok)throw new Error(Object.values(result.errors||{}).flat().map(e=>e.message).join(' ')||'Не удалось сохранить ресурс.');
      if(result.deleted)data.assignments=data.assignments.filter(a=>a.id!==result.deleted);
      else {const index=data.assignments.findIndex(a=>a.id===result.assignment.id);if(index<0)data.assignments.push(result.assignment);else data.assignments[index]=result.assignment;}
      busy=false;closeEditor();render();
    }catch(e){error(e.message);}finally{busy=false;currentEditor.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  form?.addEventListener('submit',event=>{event.preventDefault();submit();});
  document.getElementById('allocation-editor')?.addEventListener('submit',event=>{
    if(editor&&(dirty||busy)){event.preventDefault();error('Сначала сохраните или отмените изменения ресурса. Затем сохраните месячные объёмы.');editor.scrollIntoView({block:'nearest'});}
  });
  window.gprResourceEditor={wireWork,addButtons,renderWorkLists};
  if(window.wireSubworks)window.wireSubworks();
  const heading=document.querySelector('#allocation-editor h3');
  if(heading){const common=document.createElement('div');common.className='mb-3';const label=document.createElement('strong');label.textContent='Добавить ресурс вне работ';common.append(label);addButtons(common);heading.after(common);}
  render();
})();
