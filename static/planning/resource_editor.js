(() => {
  const source=document.getElementById('resource-editor-data');
  if(!source)return;
  const data=JSON.parse(source.textContent),form=document.getElementById('work-resource-form');
  const kinds={equipment:'Техника',labor:'Должность',fuel:'ГСМ'};
  const announce=state=>document.dispatchEvent(new CustomEvent('gpr-save-state',{detail:{scope:'resource',state}}));
  const format=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3});
  const field=name=>form?.elements.namedItem(name);
  const targetById=id=>data.targets.find(t=>String(t.id)===String(id));
  const composites=JSON.parse(document.getElementById('composite-items')?.textContent||'{}');
  const isComposite=id=>targetById(id)?.kind==='COMPOSITE'||Object.prototype.hasOwnProperty.call(composites,id);
  const legacyFor=work=>data.assignments.some(a=>a.legacy_package&&String(a.work)===String(work));
  let editor=null,editorTarget=null,dirty=false,busy=false;
  const foldStates=new Map(),observedScrolls=new WeakSet();
  const foldKey=host=>String(host.dataset.resourceWork||'')+'/'+String(host.dataset.resourceItem||'');
  // Record the user's choice before the deferred toggle event and any row rebuild.
  document.addEventListener('click',event=>{const summary=event.target.closest('[data-resource-fold] > summary');if(summary){const fold=summary.parentElement,host=fold.closest('[data-work-resource-target]');if(host)foldStates.set(foldKey(host),!fold.open);}},true);
  document.addEventListener('toggle',event=>{const fold=event.target;if(fold.matches?.('[data-resource-fold]')&&fold.isConnected){const host=fold.closest('[data-work-resource-target]');if(host)foldStates.set(foldKey(host),fold.open);}},true);
  const activeAssignments=()=>data.assignments.filter(a=>a.end_date>=data.start_date&&a.start_date<=data.end_date);
  const sameTarget=(assignment,work,item)=>(assignment.targets?.length ? assignment.targets.map(t=>({work:t.work_id,work_item:t.work_item_id})) : [assignment]).some(target=>String(target.work||'')===String(work||'')&&String(target.work_item||'')===String(item||''));
  function error(message) {
    const box=editor?.querySelector('[role="alert"]');
    if(box){box.textContent=message;box.hidden=!message;}if(message)announce('error');
  }
  function addButtons(container,work='',item='') {
    const host=document.createElement('div');host.dataset.workResourceTarget='';host.dataset.resourceWork=work;host.dataset.resourceItem=item;
    const fold=document.createElement('details');fold.dataset.resourceFold='';fold.open=foldStates.get(foldKey(host))??true;
    const summary=document.createElement('summary');summary.className='work-resource-summary';summary.textContent='Ресурсы';
    const list=document.createElement('div');list.dataset.workResourceList='';fold.append(summary,list);
    host.append(fold);container.append(host);renderHost(host);return host;
  }
  function wireWork(row) {
    if(data.table_mode){row.querySelector('[data-parent-resource-actions]')?.remove();return;}
    const work=row.querySelector('[name$="-work"]');
    let host=row.querySelector('[data-parent-resource-actions]');
    if(!work?.value||isComposite(work.value)&&!legacyFor(work.value)){host?.remove();return;}
    if(host && host.dataset.resourceWork===work.value)return;
    host?.remove();host=addButtons(row.querySelector('.allocation-grid'),work.value);
    host.dataset.parentResourceActions='';host.style.gridColumn='1 / -1';
  }
  function closeEditor() {if(busy)return;editor?.remove();editor=null;editorTarget=null;dirty=false;announce('clean');}
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
    const initial={assignment_id:'',resource_revision:'',resource_kind:kind,work,work_item:item,start_date:data.start_date,end_date:data.end_date,count:1,shifts_per_day:1,hours_per_shift:8,daily_liters:0,fuel_price:0,equipment_type:'',position:'',fuel_type:'',include_crew:kind==='equipment',refresh_crew:false};
    if(assignment)Object.assign(initial,assignment,{assignment_id:assignment.id,resource_revision:assignment.revision});
    // Keep the saved position editable after its last employee becomes inactive.
    // This option belongs only to this editor; new-resource forms keep the filtered list.
    const position=field('position');
    if(kind==='labor'&&assignment?.position&&position&&!Array.from(position.options).some(option=>option.value===String(assignment.position))){
      position.add(new Option(assignment.label,String(assignment.position)));
    }
    Object.entries(initial).forEach(([name,value])=>{if(name==='targets')return;const input=field(name);if(!input)return;if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value;});
    editor.querySelector('#work-resource-title').textContent=(assignment?'Изменить ресурс: ':'Добавить ресурс: ')+kinds[kind];
    editor.querySelector('#work-resource-target').textContent=work?'К работе: '+(child?target.name+' → '+child.name:(target?.name||assignment?.target||'Работа удалена из плана')):'Вне работ · общие ресурсы объекта';
    editor.querySelectorAll('[data-resource-field]').forEach(box=>{const visible=box.dataset.resourceField===kind||box.dataset.resourceField==='quantity'&&kind!=='fuel';box.hidden=!visible;box.querySelectorAll('input,select').forEach(input=>input.disabled=!visible);});
    ['equipment_type','position','fuel_type'].forEach(name=>{field(name).required=name===({equipment:'equipment_type',labor:'position',fuel:'fuel_type'}[kind]);});
    const links=assignment?.targets||((work&&!assignment?.legacy_package)?[{work_id:work,work_item_id:item||null}]:[]);
    const tokens=links.filter(t=>t.work_item_id||!isComposite(t.work_id)).map(t=>t.work_item_id?'item:'+t.work_id+':'+t.work_item_id:'work:'+t.work_id);
    const picker=editor.querySelector('[data-resource-target-picker]');
    window.workResourceTargets.set(picker,kind==='fuel'?'object':work?'works':'object',kind==='fuel'?[]:tokens);
    if(data.table_mode&&!assignment){window.workResourceTargets.set(picker,'object',[]);}
    picker.hidden=kind==='fuel'||data.table_mode;
    if(kind==='fuel')editor.querySelector('#work-resource-target').textContent='ГСМ · только общие ресурсы объекта';
    if(assignment?.legacy_package)editor.querySelector('#work-resource-target').textContent='Ранее ресурс был назначен на комплекс. Выберите работы внутри комплекса или общие ресурсы объекта.';
    field('refresh_crew').closest('p').hidden=!assignment||kind!=='equipment';
    editor.querySelector('[data-delete-resource]').hidden=!assignment;
    if(!assignment&&work&&(!target||item&&!child))error('Сначала сохраните эту работу в месячном плане. Затем добавьте ресурсы.');
    editor.addEventListener('input',()=>{dirty=true;announce('dirty');});
    editor.addEventListener('change',()=>{dirty=true;announce('dirty');});
    if(!assignment){dirty=true;announce('dirty');}
    editor.scrollIntoView({block:'nearest'});
  }
  function button(label,callback){const b=document.createElement('button');b.type='button';b.textContent=label;b.className='btn btn-sm btn-outline-primary';b.addEventListener('click',callback);return b;}
  function card(assignment,projection=null,compact=false) {
    const box=document.createElement('div');box.className=compact?'work-resource-entry':'border rounded p-3 mb-2 bg-white';box.dataset.resourceCard=String(assignment.id);box.dataset.resourceKind=assignment.resource_kind;
    const title=document.createElement('strong');title.textContent=projection?.label||assignment.label;
    const badge=document.createElement('span');badge.className='badge bg-primary ms-2';badge.textContent=assignment.shared?'Общий ресурс · работ: '+assignment.targets.length:assignment.work?'Из работы: '+assignment.target:'Вне работ';
    const detail=document.createElement('p');detail.className='small mb-2 mt-2';
    const kind=projection?.kind||assignment.resource_kind;
    detail.textContent=kind==='fuel'?format.format(Number(projection?.liters||assignment.daily_liters))+' л/день':format.format(projection?.count??assignment.count)+(kind==='equipment'?' ед.':projection?' чел. за день':' чел. на смену')+' · '+assignment.shifts_per_day+' смен/день · '+assignment.hours_per_shift+' ч/смену';
    if(data.table_mode)detail.textContent=kind==='fuel'?format.format(Number(projection?.liters||assignment.daily_liters))+' л/день':format.format(projection?.count??assignment.count)+(kind==='equipment'?' ед.':' чел.');
    const shortDate=value=>value.split('-').reverse().join('.');
    if(!data.table_mode)detail.append(document.createTextNode(' · '+shortDate(assignment.start_date)+' — '+shortDate(assignment.end_date)));box.append(title);if(!compact&&!data.table_mode)box.append(badge);box.append(detail);
    if(assignment.shared&&!data.table_mode){
      const shared=document.createElement('details');shared.className='work-resource-shared';shared.dataset.sharedResource='';
      const summary=document.createElement('summary');summary.textContent='Общий ресурс · работ: '+assignment.targets.length+' · учтён один раз';shared.append(summary);
      const links=document.createElement('ul');assignment.targets.forEach(t=>{const item=document.createElement('li');item.textContent=t.label;links.append(item);});shared.append(links);box.append(shared);
    }
    if(kind==='labor'){
      const origin=document.createElement('span');origin.className='badge bg-light text-dark d-block my-1';origin.dataset.laborOrigin='';
      origin.textContent=assignment.resource_kind==='equipment'?'Экипаж техники: '+assignment.label:'Добавлено отдельно · без техники';box.append(origin);
    }
    if(assignment.resource_kind==='equipment'&&assignment.crew&&!projection){
      const crew=document.createElement('div');crew.className='small mb-2 work-resource-crew';crew.dataset.resourceCrew='';
      const label=document.createElement('strong');label.textContent='Экипаж техники';crew.append(label);
      (assignment.projected||[]).filter(p=>p.kind==='labor').forEach(p=>{const line=document.createElement('div');line.textContent=p.label+' — '+format.format(p.count)+' чел. за день';crew.append(line);});
      box.append(crew);
    }
    if(data.can_edit){
      const edit=button('Изменить',()=>{const host=box.closest('[data-work-resource-target]');open(assignment.resource_kind,host?.dataset.resourceWork||assignment.work,host?.dataset.resourceItem||assignment.work_item,assignment,compact?host?.querySelector('[data-resource-inline-slot]'):box);});
      if(compact){const heading=document.createElement('div');heading.className='work-resource-entry-heading';box.insertBefore(heading,title);heading.append(title,edit);}else box.append(edit);
    }
    return box;
  }
  function renderHost(host) {
    let list=host.querySelector('[data-work-resource-list]');if(!list)return;
    const scrollArea=host.closest('.matrix-scroll');
    if(scrollArea&&!observedScrolls.has(scrollArea)){
      const resize=()=>{if(scrollArea.clientWidth)scrollArea.style.setProperty('--resource-panel-width',Math.max(160,scrollArea.clientWidth-20)+'px');};
      resize();if(window.ResizeObserver)new ResizeObserver(resize).observe(scrollArea);observedScrolls.add(scrollArea);
    }
    host.querySelector('[data-resource-actions]')?.remove();
    if(!host.querySelector('[data-resource-fold]')){
      const fold=document.createElement('details');fold.dataset.resourceFold='';fold.open=foldStates.get(foldKey(host))??true;
      const summary=document.createElement('summary');summary.className='work-resource-summary';fold.append(summary);host.insertBefore(fold,list);fold.append(list);
    }
    const assignments=activeAssignments().filter(a=>sameTarget(a,host.dataset.resourceWork,host.dataset.resourceItem));
    const legacyParent=Boolean(host.dataset.resourceWork&&!host.dataset.resourceItem&&isComposite(host.dataset.resourceWork));
    host.hidden=legacyParent&&!assignments.length;
    if(host.hidden)return;
    host.querySelector('.work-resource-summary').textContent=(legacyParent?'Ранее назначено на комплекс — перенесите на работу или объект · ':'Ресурсы · ')+Object.entries(kinds).map(([kind,label])=>label+': '+assignments.filter(a=>a.resource_kind===kind).length).join(' · ');
    const scroll=document.createElement('div');scroll.className='work-resource-columns';
    const table=document.createElement('table');table.className='work-resource-table';table.dataset.resourceColumns='';
    const headings=table.createTHead().insertRow(),row=assignments.length?table.createTBody().insertRow():null;
    Object.entries(kinds).filter(([kind])=>kind!=='fuel'||!host.dataset.resourceWork||assignments.some(a=>a.resource_kind==='fuel')).forEach(([kind,label])=>{
      const heading=document.createElement('th');heading.scope='col';heading.textContent=kind==='equipment'?'Техника и экипаж':kind==='labor'?'Должности':label;headings.append(heading);
      const cell=row?.insertCell();if(cell)cell.dataset.resourceColumn=kind;
      if(data.can_edit&&!legacyParent&&!(kind==='fuel'&&host.dataset.resourceWork)){const add=document.createElement('button');add.type='button';add.className='btn btn-sm btn-outline-primary work-resource-add';add.textContent='Добавить '+(kind==='equipment'?'технику':kind==='labor'?'должность':'ГСМ');add.dataset.addResource=kind;add.dataset.work=host.dataset.resourceWork;add.dataset.item=host.dataset.resourceItem;heading.append(add);}
      const selected=assignments.filter(a=>a.resource_kind===kind);
      selected.forEach(a=>cell.append(card(a,null,true)));
      if(cell&&!selected.length){const empty=document.createElement('div');empty.className='work-resource-empty';empty.textContent='—';cell.append(empty);}
    });
    scroll.append(table);
    const slot=document.createElement('div');slot.dataset.resourceInlineSlot='';
    list.replaceChildren(scroll,slot);list.hidden=false;
  }

  function renderWorkLists() {
    if(data.table_mode){document.querySelectorAll('[data-work-resource-target][data-resource-work]:not([data-resource-work=""])').forEach(h=>h.remove());return;}
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

  });
  async function submit(remove=false) {
    if(busy||!editor)return;
    if(!remove&&!form.reportValidity())return;
    busy=true;error('');announce('saving');const currentEditor=editor;currentEditor.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const payload=new FormData(form);if(remove)payload.set('action','delete');
      const response=await fetch(form.action,{method:'POST',body:payload,headers:{Accept:'application/json'},credentials:'same-origin'});
      if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw new Error(response.status===403?'Нет права изменять ресурсы этого плана. Обновите страницу.':'Не удалось сохранить ресурс. Проверьте вход в портал и повторите попытку.');
      const result=await response.json();
      if(!response.ok)throw new Error(Object.values(result.errors||{}).flat().map(e=>e.message).join(' ')||'Не удалось сохранить ресурс.');
      if(result.deleted)data.assignments=data.assignments.filter(a=>a.id!==result.deleted);
      else {const index=data.assignments.findIndex(a=>a.id===result.assignment.id);if(index<0)data.assignments.push(result.assignment);else data.assignments[index]=result.assignment;}
      busy=false;closeEditor();render();window.gprSaveUI?.updateWorkflow(result.workflow);announce('saved');
    }catch(e){error(e.message);}finally{busy=false;currentEditor.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  form?.addEventListener('submit',event=>{event.preventDefault();submit();});
  document.getElementById('allocation-editor')?.addEventListener('submit',event=>{
    if(editor&&(dirty||busy)){event.preventDefault();error('Сначала сохраните или отмените изменения ресурса. Затем сохраните месячные объёмы.');editor.scrollIntoView({block:'nearest'});}
  });
  window.gprResourceEditor={data,open,upsert:assignment=>{const index=data.assignments.findIndex(a=>a.id===assignment.id);if(index<0)data.assignments.push(assignment);else data.assignments[index]=assignment;render();},wireWork,addButtons,renderWorkLists,hasPendingChanges:()=>Boolean(editor&&(dirty||busy)),revealPending:()=>{if(editor){for(let parent=editor.parentElement;parent;parent=parent.parentElement){if(parent.tagName==='DETAILS')parent.open=true;if(parent.hasAttribute('data-work-parent')){const key=parent.dataset.workParent;document.querySelectorAll('[data-work-parent]').forEach(row=>{if(row.dataset.workParent===key)row.hidden=false;});document.querySelectorAll('[data-work-toggle]').forEach(toggle=>{if(toggle.dataset.workToggle===key){toggle.setAttribute('aria-expanded','true');toggle.textContent='▾';}});}}error('Сначала сохраните или отмените изменения этого ресурса.');editor.scrollIntoView({block:'center'});}}};
  if(window.wireSubworks)window.wireSubworks();
  render();
})();
