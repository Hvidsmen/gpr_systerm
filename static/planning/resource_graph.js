(() => {
 'use strict';
 const root=document.querySelector('[data-resource-graph]'),source=document.getElementById('resource-graph-data');
 if(!root||!source||!window.gprResourceEditor)return;
 const cfg=JSON.parse(source.textContent),api=window.gprResourceEditor,data=api.data;
 const q=s=>root.querySelector(s),dialog=document.querySelector('[data-graph-dialog]'),dq=s=>dialog.querySelector(s);
 const board=q('[data-graph-board]'),svg=q('[data-graph-wires]'),status=q('[data-graph-status]');
 const moneyless=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2});
 const token=t=>t.token||(t.work_item_id?'item:'+t.work_id+':'+t.work_item_id:'work:'+t.work_id);
 const norm=s=>s.toLocaleLowerCase('ru').replace(/ё/g,'е');
 const date=s=>s.split('-').reverse().join('.');
 const range=a=>date(a.start_date)+' — '+date(a.end_date);
 const active=a=>a.end_date>=data.start_date&&a.start_date<=data.end_date;
 const nodes=new Map(cfg.nodes.map(n=>[n.token,n])),groups=new Map();
 // The menu also offers saved works in the other months of this plan.
 const choices=new Map(nodes);
 for(const work of data.targets){
  const hierarchy={object_id:String(work.object_id||''),object_name:work.object_name,package_id:work.kind==='COMPOSITE'?String(work.id):'',package_name:work.kind==='COMPOSITE'?work.name:''};
  if(work.kind!=='COMPOSITE')choices.set('work:'+work.id,choices.get('work:'+work.id)||{...hierarchy,token:'work:'+work.id,label:work.name,name:work.name,group:'Самостоятельные работы'});
  for(const item of work.items)choices.set('item:'+work.id+':'+item.id,choices.get('item:'+work.id+':'+item.id)||{...hierarchy,token:'item:'+work.id+':'+item.id,label:work.name+' → '+item.name,name:item.name,group:work.name});
 }

 const unique=items=>[...new Map(items.map(i=>[String(i.value),{value:String(i.value),label:i.label}])).values()].sort((a,b)=>a.label.localeCompare(b.label,'ru'));
 function hierarchyFilters(container,onchange){
  let filters;
  const change=()=>{refresh();onchange();};
  filters={object:window.graphMultiselect.create(container,'Комплексные объекты',[],change),package:window.graphMultiselect.create(container,'Комплексные работы',[],change),work:window.graphMultiselect.create(container,'Работы',[],change)};
  function refresh(){
   const values=[...choices.values()];filters.object.setOptions(unique(values.map(n=>({value:n.object_id||'',label:n.object_name||'Без комплексного объекта'}))));
   const objects=filters.object.values();const inObjects=values.filter(n=>!objects.length||objects.includes(n.object_id||''));
   filters.package.setOptions(unique(inObjects.filter(n=>n.package_id).map(n=>({value:n.package_id,label:(n.object_name?n.object_name+' → ':'')+n.package_name}))));
   const packages=filters.package.values();filters.work.setOptions(unique(inObjects.filter(n=>!packages.length||packages.includes(n.package_id)).map(n=>({value:n.token,label:n.label}))));
  }
  function matches(n){const objects=filters.object.values(),packages=filters.package.values(),works=filters.work.values();return (!objects.length||objects.includes(n.object_id||''))&&(!packages.length||packages.includes(n.package_id||''))&&(!works.length||works.includes(n.token));}
  refresh();return {filters,matches,clear:()=>{Object.values(filters).forEach(f=>f.set([]));refresh();}};
 }
 const graphFilters=hierarchyFilters(q('[data-graph-selectors]'),()=>render());
 const gearFilter=window.graphMultiselect.create(q('[data-graph-selectors]'),'Техника',[],()=>render());
 const roleFilter=window.graphMultiselect.create(q('[data-graph-selectors]'),'Должности',[],()=>render());
 function refreshResourceFilters(){
  gearFilter.setOptions(unique(data.assignments.filter(a=>a.resource_kind==='equipment').map(a=>({value:a.equipment_type,label:a.label}))));
  roleFilter.setOptions(unique(data.assignments.flatMap(a=>a.resource_kind==='labor'?[{value:a.position,label:a.label}]:(a.crew_people||[]).map(p=>({value:p.id,label:p.name})))));
 }
 function matchResource(a){
  const gear=gearFilter.values(),roles=roleFilter.values();
  const roleMatch=roles.includes(String(a.position))||(a.crew_people||[]).some(p=>roles.includes(String(p.id)));
  if(gear.length&&roles.length)return a.resource_kind==='equipment'&&gear.includes(String(a.equipment_type))||a.resource_kind==='labor'&&roleMatch;
  if(gear.length)return a.resource_kind==='equipment'&&gear.includes(String(a.equipment_type));
  return !roles.length||roleMatch;
 }
 const dialogFilters=hierarchyFilters(dq('[data-graph-dialog-selectors]'),()=>filterDialogRows());
 function filterDialogRows(){const words=norm(dq('[data-graph-dialog-search]').value).split(/\s+/).filter(Boolean);for(const row of dq('[data-graph-link-rows]').children)row.hidden=!dialogFilters.matches(choices.get(row.dataset.token))||!words.every(w=>row.dataset.search.includes(w));}
 let selected=null,pendingSource=null,drag=null,dragPath=null,editing=null,saving=false,dialogDirty=false,frame=null,layoutAnchor=null,requestedAnchor=null;
 const setStatus=(state,text)=>{status.dataset.state=state;status.textContent=text;};
 function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
 function button(text,callback,cls=''){const b=el('button',cls,text);b.type='button';b.addEventListener('click',callback);return b;}
 function announce(text){q('[data-graph-hint]').textContent=text;}
 function canChange(){if(!cfg.can_edit)return false;if(api.hasPendingChanges()){api.revealPending();return false;}return true;}
 function links(a){return a.targets||[];}
 function select(type,id){selected=selected?.type===type&&selected.id===id?null:{type,id};pendingSource=null;highlight();}
 function highlight(){
  const relatedWorks=new Set(),relatedResources=new Set();
  if(selected?.type==='work'){
   relatedWorks.add(selected.id);
   for(const a of data.assignments.filter(active))if(links(a).some(t=>token(t)===selected.id))relatedResources.add(String(a.id));
  }else if(selected){relatedResources.add(selected.id);const a=data.assignments.find(a=>String(a.id)===selected.id);for(const t of a?links(a):[])relatedWorks.add(token(t));}
  root.querySelectorAll('[data-graph-node]').forEach(node=>{const yes=node.dataset.graphNode==='work'?relatedWorks.has(node.dataset.key):relatedResources.has(node.dataset.key);node.classList.toggle('is-selected',Boolean(selected&&yes));node.classList.toggle('is-muted',Boolean(selected&&!yes));});
  svg.querySelectorAll('[data-edge]').forEach(path=>{const yes=relatedWorks.has(path.dataset.work)&&relatedResources.has(path.dataset.resource);path.classList.toggle('is-active',Boolean(selected&&yes));path.classList.toggle('is-muted',Boolean(selected&&!yes));});
 }
 function port(type,key){const p=el(cfg.can_edit?'button':'span','rg-port');p.dataset.graphPort=type;p.dataset.key=key;if(cfg.can_edit){p.type='button';p.setAttribute('aria-label',type==='work'?'Провести связь от работы':'Соединить ресурс с выбранной работой');}else p.setAttribute('aria-hidden','true');return p;}
 function resourceCard(a,fuel=false){
  const card=el('article','rg-node rg-resource-node');card.dataset.graphNode='resource';card.dataset.key=String(a.id);card.dataset.kind=a.resource_kind;
  const title=button(a.label,()=>select('resource',String(a.id)),'rg-node-title');card.append(title);
  card.append(el('p','rg-node-meta',(fuel?'':a.resource_kind==='equipment'?'Техника · ':'Должность · ')+(a.quantity_label||(fuel?moneyless.format(Number(a.daily_liters))+' л/день':a.count+(a.resource_kind==='equipment'?' ед.':' чел.')))));
  if(a.resource_kind==='equipment'&&a.include_crew){
   const crew=el('details','rg-crew');crew.append(el('summary','',a.include_crew?'Экипаж':'Без экипажа'));if(a.include_crew)crew.append(el('div','',a.crew||'не указан'));card.append(crew);
  }
  if(!fuel)card.append(port('resource',String(a.id)));
  const foot=el('div','rg-node-foot');foot.append(el('span','',fuel?'Только объект':a.targets.length?'Связей с работами: '+a.targets.length:'Общие ресурсы объекта'));
  if(!fuel){const menu=button(cfg.can_edit?'Настроить связи':'Посмотреть связи',()=>openDialog(a));menu.dataset.graphMenu=String(a.id);foot.append(menu);}
  if(cfg.can_edit&&a.resource_kind==='equipment'&&!a.table_resource){const crew=button('Настроить экипаж',()=>{if(canChange())document.dispatchEvent(new CustomEvent('gpr-open-crew',{detail:{assignment:a}}));});crew.dataset.graphCrew=String(a.id);foot.append(crew);}
  if(cfg.can_edit){const edit=button('Изменить ресурс',()=>{if(!canChange())return;if(a.open_table_url){window.location.assign(a.open_table_url);return;}api.open(a.resource_kind,a.work,a.work_item,a,q('[data-graph-editor]'));});edit.dataset.graphEdit=String(a.id);foot.append(edit);}
  card.append(foot);
  if(!fuel&&a.targets.length){const listing=el('details','rg-connection-list');listing.append(el('summary','', 'Связанные работы'));for(const t of a.targets){listing.append(button(choices.get(token(t))?.name||t.item_name||t.work_name,()=>openDialog(a,token(t))));}card.append(listing);}
  return card;
 }
 function captureAnchor(resourceId=null){
  const visible=node=>{const r=node.getBoundingClientRect();return r.bottom>100&&r.top<innerHeight-40;};
  const focused=document.activeElement?.closest('[data-graph-node]');
  const preferred=editing&&resourceNode(String(editing.id));
  const card=[resourceId&&resourceNode(String(resourceId)),focused,preferred,...root.querySelectorAll('[data-graph-node]')].find(node=>node&&visible(node));
  return card?{kind:card.dataset.graphNode,key:card.dataset.key,top:card.getBoundingClientRect().top}:null;
 }
 function restoreAnchor(){
  if(!layoutAnchor)return;
  const anchor=layoutAnchor;layoutAnchor=null;
  const card=root.querySelector('[data-graph-node="'+anchor.kind+'"][data-key="'+CSS.escape(anchor.key)+'"]');
  if(card){const offset=card.getBoundingClientRect().top-anchor.top;if(Math.abs(offset)>1)window.scrollBy({top:offset,behavior:'instant'});}
 }
 function workCard(n,regular,grouped=false){
  const card=el('article','rg-node rg-work-node');card.dataset.graphNode='work';card.dataset.key=n.token;
  if(!grouped)card.append(el('p','rg-node-path',n.package_name?[n.object_name,n.package_name].filter(Boolean).join(' → '):n.group==='Самостоятельные работы'?(n.object_name||n.group):n.group));
  card.append(button(n.name,()=>select('work',n.token),'rg-node-title'));
  const count=regular.filter(a=>links(a).some(t=>token(t)===n.token)).length;
  card.append(el('p','rg-node-meta',count?'Связанных ресурсов: '+count:'Ресурсы ещё не назначены'));
  if(!n.planned)card.append(el('p','rg-node-meta','Нет объёма в выбранном месяце'));
  card.append(port('work',n.token));return card;
 }
 function layoutBlock(host,key){
  const block=el('div','rg-layout-block'),content=el('div','rg-block-content');block.dataset.graphCluster=key;block.append(content);host.append(block);return content;
 }
 function alignBlocks(){
  const narrow=window.innerWidth<=850;
  for(const block of q('[data-graph-works]').children){
   const peer=q('[data-graph-resources]').querySelector('[data-graph-cluster="'+CSS.escape(block.dataset.graphCluster)+'"]');if(!peer)continue;
   const naturalHeight=node=>{const style=getComputedStyle(node);return node.firstElementChild.getBoundingClientRect().height+parseFloat(style.paddingTop)+parseFloat(style.paddingBottom)+parseFloat(style.borderTopWidth)+parseFloat(style.borderBottomWidth);};
   const height=narrow?'':Math.ceil(Math.max(naturalHeight(block),naturalHeight(peer)))+'px';
   if(block.style.minHeight!==height)block.style.minHeight=peer.style.minHeight=height;
  }
 }
 function render(){
  layoutAnchor=requestedAnchor||captureAnchor();requestedAnchor=null;
  if(selected?.type==='resource'&&!data.assignments.some(a=>String(a.id)===selected.id))selected=null;
  refreshResourceFilters();
  root.querySelectorAll('.rg-group').forEach(g=>groups.set(g.dataset.group,g.open));
  const search=norm(q('[data-graph-search]').value).split(/\s+/).filter(Boolean),filter=q('[data-graph-filter]').value;
  const matches=text=>search.every(w=>norm(text).includes(w));
  const assignments=data.assignments.filter(active),regular=assignments.filter(a=>a.resource_kind!=='fuel');
  let visibleResources=regular.filter(a=>matchResource(a)&&(filter==='all'||filter==='unlinked'&&!links(a).length||filter===a.resource_kind)&&(!search.length||matches(a.label+' '+a.crew)||links(a).some(t=>matches(choices.get(token(t))?.label||t.label))));
  const connected=new Set(visibleResources.flatMap(a=>links(a).map(token)));
  const visibleNodes=cfg.nodes.filter(n=>graphFilters.matches(choices.get(n.token)||n)&&(!search.length||matches(n.label)||connected.has(n.token)));
  const workHost=q('[data-graph-works]'),resourceHost=q('[data-graph-resources]');workHost.replaceChildren();resourceHost.replaceChildren();
  const layout=window.resourceGraphLayout.arrange(visibleNodes,visibleResources.map(a=>({...a,workTokens:links(a).map(token)})));
  for(const component of layout.components){
   const left=layoutBlock(workHost,component.key),right=layoutBlock(resourceHost,component.key),cards=el('div','rg-resource-cards');
   component.works.forEach(n=>left.append(workCard(n,regular)));
   component.resources.forEach(a=>cards.append(resourceCard(a)));right.append(cards);
  }
  if(layout.unlinkedWorks.length||layout.unlinkedResources.length||layout.hiddenResources.length||!visibleNodes.length||!visibleResources.length){
   const left=layoutBlock(workHost,'unlinked'),right=layoutBlock(resourceHost,'unlinked'),sections=new Map();
   if(layout.unlinkedWorks.length)left.append(el('div','rg-eyebrow','Работы без видимых связей'));
   for(const n of layout.unlinkedWorks){
    let group=sections.get(n.group);if(!group){group=el('details','rg-group');group.dataset.group=n.group;group.open=groups.get(n.group)??true;group.append(el('summary','',n.group));sections.set(n.group,group);left.append(group);}
    group.append(workCard(n,regular,true));
   }
   if(!visibleNodes.length)left.append(el('p','rg-empty',search.length?'Работы не найдены':'Нет работ по выбранным условиям. Проверьте фильтры и месячный план.'));
   for(const [items,title] of [[layout.hiddenResources,'Связанные работы скрыты фильтрами'],[layout.unlinkedResources,'Общие ресурсы объекта']]){
    if(!items.length)continue;right.append(el('div','rg-eyebrow',title));const cards=el('div','rg-resource-cards');items.forEach(a=>cards.append(resourceCard(a)));right.append(cards);
   }
   if(!visibleResources.length)right.append(el('p','rg-empty',regular.length?'Нет ресурсов по выбранным условиям':'Добавьте технику или людей. Затем соедините их с работами.'));
  }
  const fuel=q('[data-graph-fuel]');fuel.replaceChildren();assignments.filter(a=>a.resource_kind==='fuel').forEach(a=>fuel.append(resourceCard(a,true)));if(!fuel.children.length)fuel.append(el('p','rg-empty','ГСМ на этот месяц пока не добавлены.'));
  q('[data-graph-work-count]').textContent=String(visibleNodes.length);q('[data-graph-resource-count]').textContent=String(visibleResources.length);
  scheduleDraw();highlight();
 }
 function point(card,side){const r=card.getBoundingClientRect(),b=board.getBoundingClientRect();return {x:(side==='work'?r.right:r.left)-b.left,y:r.top-b.top+33};}
 function curve(a,b){const reach=Math.max(45,Math.abs(b.x-a.x)*.45);return `M ${a.x},${a.y} C ${a.x+reach},${a.y} ${b.x-reach},${b.y} ${b.x},${b.y}`;}
 function connectionCurve(work,resource){
  const from=point(work,'work'),to=point(resource,'resource'),lane=q('.rg-resources').getBoundingClientRect(),bounds=board.getBoundingClientRect();
  if(resource.getBoundingClientRect().left-lane.left<40)return curve(from,to);
  // Reach the second card through the space above its row, then the column gap.
  const approach={x:lane.left-bounds.left-12,y:resource.getBoundingClientRect().top-bounds.top-10};
  return curve(from,approach)+` L ${to.x-10},${approach.y} L ${to.x-10},${to.y} L ${to.x},${to.y}`;
 }
 function path(d,cls){const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',d);p.setAttribute('class',cls);return p;}
 function draw(){
  alignBlocks();restoreAnchor();
  svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${board.clientWidth} ${board.scrollHeight}`);
  if(window.innerWidth<=850)return;
  for(const a of data.assignments.filter(a=>active(a)&&a.resource_kind!=='fuel')){
   const end=resourceNode(String(a.id));if(!end||!end.getClientRects().length)continue;
   for(const t of links(a)){
    const work=root.querySelector('[data-graph-node="work"][data-key="'+CSS.escape(token(t))+'"]');if(!work||!work.getClientRects().length)continue;
    const d=connectionCurve(work,end),wire=path(d,'rg-wire'),hit=path(d,'rg-wire-hit');
    wire.dataset.edge='';wire.dataset.work=token(t);wire.dataset.resource=String(a.id);wire.dataset.kind=a.resource_kind;
    hit.setAttribute('role','button');hit.setAttribute('tabindex','0');hit.setAttribute('aria-label','Связь: '+(choices.get(token(t))?.name||t.label)+' — '+a.label);
    hit.addEventListener('click',()=>openDialog(a,token(t)));hit.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openDialog(a,token(t));}});
    svg.append(wire,hit);
   }
  }
  highlight();
 }
 function scheduleDraw(){if(frame)cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{frame=null;draw();});}
 function resourceNode(id){return root.querySelector('[data-graph-node="resource"][data-key="'+CSS.escape(id)+'"]');}
 function updateSelection(){
  const object=dq('[data-graph-object]').checked;
  for(const row of dq('[data-graph-link-rows]').children){const checked=row.querySelector('[type=checkbox]');checked.disabled=!cfg.can_edit||object;}
  const count=Array.from(dq('[data-graph-link-rows]').querySelectorAll('[type=checkbox]')).filter(i=>i.checked).length;
  dq('[data-graph-selection-count]').textContent=object?'Общие ресурсы объекта':'Выбрано работ: '+count;
 }
 function openDialog(a,focusToken=null,add=false){
  if(dialog.open||saving)return;if(cfg.can_edit&&!canChange())return;
  editing=a;dialogDirty=add;dq('[data-graph-error]').hidden=true;dq('#rg-dialog-title').textContent=a.label;dq('[data-graph-dialog-meta]').textContent=a.count+' '+(a.resource_kind==='equipment'?'ед.':'чел. на смену')+' · общее количество';
  dq('[data-graph-object]').checked=!a.targets.length&&!add;dq('[data-graph-object]').disabled=!cfg.can_edit;
  const host=dq('[data-graph-link-rows]');host.replaceChildren();
  const selected=new Set(a.targets.map(token));if(add)selected.add(focusToken);
  for(const [key,n] of choices){
   const row=el('div','rg-link-row');row.dataset.token=key;row.dataset.search=norm(n.label);
   const label=el('label'),check=el('input');check.type='checkbox';check.value=key;check.checked=selected.has(key);label.append(check,document.createTextNode(n.label));row.append(label);
   host.append(row);
  }
  dialogFilters.clear();dq('[data-graph-dialog-search]').value='';dq('[data-graph-save]').hidden=!cfg.can_edit;updateSelection();dialog.showModal();
  if(focusToken){const row=host.querySelector('[data-token="'+CSS.escape(focusToken)+'"]');row?.scrollIntoView({block:'center'});row?.querySelector('[type=checkbox]')?.focus();}
  if(add)setStatus('dirty','Есть изменения связей');
 }
 function closeDialog(){if(saving)return;if(dialogDirty&&!window.confirm('Отменить несохранённые изменения связей?'))return;dialogDirty=false;dialog.close();setStatus('saved','Сохранено');}
 dq('[data-graph-links-form]').addEventListener('change',event=>{if(event.target.closest('.rg-multi'))return;dialogDirty=true;updateSelection();setStatus('dirty','Есть изменения связей');});
 dq('[data-graph-links-form]').addEventListener('input',event=>{if(event.target.matches('[data-graph-dialog-search]')||event.target.closest('.rg-multi'))return;dialogDirty=true;setStatus('dirty','Есть изменения связей');});
 dq('[data-graph-dialog-search]').addEventListener('input',filterDialogRows);
 dialog.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.type==='search')event.preventDefault();});
 dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog();});dialog.querySelectorAll('[data-graph-cancel]').forEach(b=>b.addEventListener('click',closeDialog));
 dq('[data-graph-links-form]').addEventListener('submit',async event=>{
  event.preventDefault();if(saving||!cfg.can_edit||!editing)return;
  const object=dq('[data-graph-object]').checked,chosen=Array.from(dq('[data-graph-link-rows]').children).filter(row=>row.querySelector('[type=checkbox]').checked&&!object);
  const error=dq('[data-graph-error]');error.hidden=true;
  if(!object&&!chosen.length){error.hidden=false;error.textContent='Выберите работы или общие ресурсы объекта.';return;}
  saving=true;setStatus('saving','Сохранение связей…');dq('[data-graph-save]').disabled=true;
  try{
   const response=await fetch(cfg.save_url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json','X-CSRFToken':document.querySelector('#work-resource-form [name=csrfmiddlewaretoken]').value},body:JSON.stringify({assignment_id:editing.id,revision:editing.revision,targets:chosen.map(row=>row.dataset.token)})});
   if(!(response.headers.get('content-type')||'').includes('application/json'))throw new Error('Не удалось сохранить связи. Проверьте доступ к плану.');
   const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить связи.');
   requestedAnchor=captureAnchor(result.assignment.id);dialogDirty=false;dialog.close();api.upsert(result.assignment);setStatus('saved','Связи сохранены');announce('Связи сохранены. Схема упорядочена автоматически. Количество ресурса и экипаж не изменены.');resourceNode(String(result.assignment.id))?.querySelector('[data-graph-menu]')?.focus({preventScroll:true});
  }catch(e){error.hidden=false;error.textContent=e.message;setStatus('error','Связи не сохранены');}
  finally{saving=false;dq('[data-graph-save]').disabled=false;}
 });
 root.querySelectorAll('[data-graph-add]').forEach(b=>b.addEventListener('click',()=>{if(canChange())api.open(b.dataset.graphAdd,'','',null,q('[data-graph-editor]'));}));
 q('[data-graph-search]').addEventListener('input',render);q('[data-graph-filter]').addEventListener('change',render);q('[data-graph-reset]').addEventListener('click',()=>{graphFilters.clear();gearFilter.set([]);roleFilter.set([]);q('[data-graph-search]').value='';q('[data-graph-filter]').value='all';selected=null;pendingSource=null;render();announce('Нажмите работу или ресурс, чтобы выделить связи.');});
 board.addEventListener('toggle',scheduleDraw,true);
 board.addEventListener('pointerdown',event=>{
  const p=event.target.closest('[data-graph-port="work"]');if(!p||!canChange()||event.button!==0)return;
  drag={key:p.dataset.key,startX:event.clientX,startY:event.clientY,moved:false,from:point(p.closest('.rg-node'),'work')};p.setPointerCapture(event.pointerId);
 });
 board.addEventListener('pointermove',event=>{
  if(!drag)return;if(Math.hypot(event.clientX-drag.startX,event.clientY-drag.startY)>5)drag.moved=true;
  if(!drag.moved)return;const b=board.getBoundingClientRect();if(!dragPath){dragPath=path('','rg-wire-preview');svg.append(dragPath);}dragPath.setAttribute('d',curve(drag.from,{x:event.clientX-b.left,y:event.clientY-b.top}));
  const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-graph-node="resource"]');root.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));if(target&&target.dataset.kind!=='fuel')target.classList.add('is-drop-target');
 });
 board.addEventListener('pointerup',event=>{
  if(!drag)return;const current=drag;drag=null;dragPath?.remove();dragPath=null;root.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
  const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-graph-node="resource"]');
  if(current.moved&&target&&target.dataset.kind!=='fuel'){pendingSource=null;const a=data.assignments.find(a=>String(a.id)===target.dataset.key);openDialog(a,current.key,true);}
  else if(!current.moved){pendingSource=current.key;announce('Теперь нажмите точку у нужной техники или человека.');}
 });
 board.addEventListener('pointercancel',()=>{drag=null;dragPath?.remove();dragPath=null;root.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));});
 board.addEventListener('click',event=>{const p=event.target.closest('[data-graph-port]');if(!p||!cfg.can_edit||dialog.open)return;if(p.dataset.graphPort==='work'){if(!drag)pendingSource=p.dataset.key;return;}if(pendingSource){const key=pendingSource;pendingSource=null;openDialog(data.assignments.find(a=>String(a.id)===p.dataset.key),key,true);}});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){pendingSource=null;selected=null;highlight();}});
 window.addEventListener('beforeunload',event=>{if(dialogDirty||saving||api.hasPendingChanges()){event.preventDefault();event.returnValue='';}});
 document.addEventListener('gpr-resource-rendered',render);
 document.addEventListener('gpr-graph-anchor',event=>{requestedAnchor=captureAnchor(event.detail?.id);});
 document.addEventListener('gpr-save-state',event=>{const state=event.detail.state;setStatus(state,({dirty:'Есть изменения ресурса',saving:'Сохранение ресурса…',saved:'Сохранено',clean:'Сохранено',error:'Есть несохранённые изменения'})[state]||'Сохранено');});
 new ResizeObserver(scheduleDraw).observe(board);window.addEventListener('resize',scheduleDraw);
 render();
})();
