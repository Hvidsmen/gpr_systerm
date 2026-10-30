(() => {
 'use strict';
 const $=id=>document.getElementById(id), read=id=>JSON.parse($(id).textContent);
 let config=read('pivot-initial'); const fields=read('pivot-fields'),base=read('pivot-base-measures');
 const defaults={rows:['building','complex_object','package','work'],columns:['month'],measures:['plan','fact','delta'],formulas:[],view:'hierarchy',totals:{row_levels:['building','complex_object','package'],column_levels:[],row_grand:true,column_grand:true,position:'after'}};
 if($('pivot-form').elements.plan_type){defaults.rows=['building','section','complex_object','package','work'];defaults.totals.row_levels=['building','section','complex_object','package'];if($('pivot-form').elements.plan_type.value==='object')defaults.columns=[];}
 const button=(text,fn,label)=>{const b=document.createElement('button');b.type='button';b.textContent=text;if(label)b.setAttribute('aria-label',label);b.addEventListener('click',fn);return b;};
 let dragging=null;
 // Remember explicit choices even while a field is the leaf (no subtotal there).
 const totalChoices={rows:new Map(),columns:new Map()};
 function clearDropHints(){document.querySelectorAll('.pivot-drop-before,.pivot-drop-after,.pivot-drop-target,.pivot-dragging').forEach(el=>el.classList.remove('pivot-drop-before','pivot-drop-after','pivot-drop-target','pivot-dragging'));}
 function endDrag(){dragging=null;clearDropHints();}
 function insertionTarget(list,y){
  return [...list.children].filter(el=>el.dataset.field!==dragging?.field).find(el=>y<el.getBoundingClientRect().top+el.getBoundingClientRect().height/2)||null;
 }
 function moveDragged(axis,before){
  if(!dragging||!config[dragging.axis].includes(dragging.field))return;
  const field=dragging.field;config[dragging.axis]=config[dragging.axis].filter(key=>key!==field);
  const position=before?config[axis].indexOf(before):config[axis].length;
  config[axis].splice(position<0?config[axis].length:position,0,field);
  endDrag();render();
  const row=[...$('pivot-'+axis).children].find(el=>el.dataset.field===field);row?.focus();
  $('pivot-drag-status').textContent=fields[field]+': '+(axis==='rows'?'строки':'столбцы')+', позиция '+(config[axis].indexOf(field)+1);
 }
 for(const axis of ['rows','columns']){
  const list=$('pivot-'+axis);
  list.addEventListener('dragover',event=>{
   if(!dragging)return;event.preventDefault();event.dataTransfer.dropEffect='move';
   document.querySelectorAll('.pivot-drop-before,.pivot-drop-after,.pivot-drop-target').forEach(el=>el.classList.remove('pivot-drop-before','pivot-drop-after','pivot-drop-target'));
   list.classList.add('pivot-drop-target');const before=insertionTarget(list,event.clientY);
   if(before)before.classList.add('pivot-drop-before');
   else [...list.children].filter(el=>el.dataset.field!==dragging.field).at(-1)?.classList.add('pivot-drop-after');
  });
  list.addEventListener('dragleave',event=>{if(!list.contains(event.relatedTarget)){list.classList.remove('pivot-drop-target');list.querySelectorAll('.pivot-drop-before,.pivot-drop-after').forEach(el=>el.classList.remove('pivot-drop-before','pivot-drop-after'));}});
  list.addEventListener('drop',event=>{if(!dragging)return;event.preventDefault();const before=insertionTarget(list,event.clientY);moveDragged(axis,before?.dataset.field);});
 }
 function renderTotals(){
  config.view=config.view||'table';config.totals=config.totals||{row_levels:[],column_levels:[],row_grand:false,column_grand:false,position:'after'};
  if($('pivot-view'))$('pivot-view').value=config.view;
  if($('pivot-total-position'))$('pivot-total-position').value=config.totals.position;
  for(const axis of ['rows','columns']){
   const key=axis==='rows'?'row_levels':'column_levels',available=config[axis].slice(0,-1),box=$(axis==='rows'?'pivot-row-subtotals':'pivot-column-subtotals');
   const choices=totalChoices[axis];
   if(!choices.size)for(const [index,field] of config[axis].entries())choices.set(field,index===config[axis].length-1||config.totals[key].includes(field));
   for(const field of available)if(!choices.has(field))choices.set(field,true);
   config.totals[key]=available.filter(field=>choices.get(field));
   if(!box)continue;box.replaceChildren();
   for(const field of available){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=config.totals[key].includes(field);input.addEventListener('change',()=>{choices.set(field,input.checked);config.totals[key]=available.filter(k=>choices.get(k));});label.append(input,document.createTextNode(' '+fields[field]));box.append(label);}
   if(available.length)box.append(button('Включить все',()=>{for(const field of available)choices.set(field,true);renderTotals();},'Включить все подытоги '+(axis==='rows'?'строк':'столбцов')));
   if(!available.length){const note=document.createElement('small');note.textContent='Добавьте второй уровень на эту ось, чтобы включить подытоги.';box.append(note);}
  }
  if($('pivot-row-grand'))$('pivot-row-grand').checked=config.totals.row_grand;
  if($('pivot-column-grand'))$('pivot-column-grand').checked=config.totals.column_grand;
 }
 function render(){
  renderTotals();
  for(const axis of ['rows','columns']){
   const ol=$('pivot-'+axis);ol.replaceChildren();
   config[axis].forEach((key,index)=>{const li=document.createElement('li'),span=document.createElement('span'),handle=document.createElement('span');
    li.dataset.field=key;li.draggable=true;li.tabIndex=-1;li.setAttribute('aria-label',fields[key]);
    handle.className='pivot-drag-handle';handle.textContent='⠿';handle.setAttribute('aria-hidden','true');handle.title='Перетащите для изменения порядка или переноса на другую ось';
    span.textContent=fields[key];li.append(handle,span);
    let fromButton=false;li.addEventListener('pointerdown',event=>{fromButton=!!event.target.closest('button');});
    li.addEventListener('dragstart',event=>{if(fromButton){event.preventDefault();return;}dragging={axis,field:key};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',key);li.classList.add('pivot-dragging');});
    li.addEventListener('dragend',endDrag);
    li.append(button('↑',()=>{if(index){[config[axis][index-1],config[axis][index]]=[key,config[axis][index-1]];render();}},'Выше: '+fields[key]));
    li.append(button('↓',()=>{if(index<config[axis].length-1){[config[axis][index+1],config[axis][index]]=[key,config[axis][index+1]];render();}},'Ниже: '+fields[key]));
    li.append(button('⇄',()=>{config[axis].splice(index,1);config[axis==='rows'?'columns':'rows'].push(key);render();},'Перенести на другую ось: '+fields[key]));
    li.append(button('×',()=>{config[axis].splice(index,1);render();},'Убрать: '+fields[key]));ol.append(li);
   });
   const select=$(axis==='rows'?'pivot-row-add':'pivot-column-add');select.replaceChildren();
   Object.entries(fields).filter(([key])=>![...config.rows,...config.columns].includes(key)).forEach(([key,label])=>{const o=document.createElement('option');o.value=key;o.textContent=label;select.append(o);});
  }
  const target=$('pivot-measures');target.replaceChildren();
  const measures=[...Object.entries(base),...config.formulas.map(f=>[f.id,f.name])];
  measures.forEach(([id,label])=>{const line=document.createElement('div'),l=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=config.measures.includes(id);input.addEventListener('change',()=>{config.measures=input.checked?[...config.measures,id]:config.measures.filter(v=>v!==id);});l.append(input,document.createTextNode(' '+label));line.append(l);
   if(id.startsWith('custom_'))line.append(button('×',()=>{const remaining=config.formulas.filter(f=>f.id!==id),chosen=new Set(config.measures);config.formulas=remaining.map((f,i)=>({...f,id:'custom_'+i}));config.measures=config.measures.filter(v=>!v.startsWith('custom_'));remaining.forEach((f,i)=>{if(chosen.has(f.id))config.measures.push('custom_'+i);});render();},'Удалить формулу '+label));target.append(line);
  });
 }
 document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>{const axis=b.dataset.add,key=$(axis==='rows'?'pivot-row-add':'pivot-column-add').value;if(key){config[axis].push(key);render();}}));
 $('formula-add').addEventListener('click',()=>{const name=$('formula-name').value.trim(),expression=$('formula-expression').value.trim();if(!name||!expression){alert('Укажите название и формулу.');return;}if(config.formulas.length>=10){alert('Можно добавить до 10 формул.');return;}const id='custom_'+config.formulas.length;config.formulas.push({id,name,expression,percent:$('formula-percent').checked});config.measures.push(id);$('formula-name').value=$('formula-expression').value='';render();});
 $('pivot-form').addEventListener('submit',()=>{$('pivot-config').value=JSON.stringify(config);});
 $('pivot-save').addEventListener('submit',()=>{$('pivot-save-config').value=JSON.stringify(config);});
 $('pivot-update').addEventListener('click',()=>{if(!$('pivot-preset').value){alert('Выберите вариант для обновления.');return;}$('pivot-save-preset').value=$('pivot-preset').value;$('pivot-save').requestSubmit();});
 $('pivot-load').addEventListener('click',()=>{const u=new URL(location.href);u.searchParams.delete('config');u.searchParams.delete('export');if($('pivot-preset').value)u.searchParams.set('preset',$('pivot-preset').value);else u.searchParams.delete('preset');location.assign(u);});
 for(const action of ['default','delete'])$('pivot-'+action).addEventListener('click',()=>{const id=$('pivot-preset').value;if(!id){alert('Выберите сохранённый вариант.');return;}if(action==='delete'&&!confirm('Удалить этот личный вариант?'))return;const form=$('pivot-save');form.elements.namedItem('action').value=action;$('pivot-save-preset').value=id;form.requestSubmit();});
 for(const [id,key] of [['pivot-row-grand','row_grand'],['pivot-column-grand','column_grand']])$(id)?.addEventListener('change',event=>{config.totals[key]=event.target.checked;});
 $('pivot-view')?.addEventListener('change',event=>{config.view=event.target.value;});
 $('pivot-total-position')?.addEventListener('change',event=>{config.totals.position=event.target.value;});
 const collapsed=new Set(),treeButtons=[...document.querySelectorAll('[data-pivot-node]')];
 function updateTree(){
  document.querySelectorAll('#pivot-result tbody tr[data-pivot-ancestors]').forEach(row=>{row.hidden=row.dataset.pivotAncestors.split(' ').some(id=>collapsed.has(id));});
  treeButtons.forEach(b=>{const expanded=!collapsed.has(b.dataset.pivotNode);b.setAttribute('aria-expanded',String(expanded));b.textContent=expanded?'▾':'▸';b.setAttribute('aria-label',(expanded?'Свернуть':'Развернуть')+' группу '+b.nextElementSibling.textContent);});
 }
 treeButtons.forEach(b=>b.addEventListener('click',()=>{const id=b.dataset.pivotNode;collapsed.has(id)?collapsed.delete(id):collapsed.add(id);updateTree();}));
 $('pivot-expand-all')?.addEventListener('click',()=>{collapsed.clear();updateTree();});
 $('pivot-collapse-all')?.addEventListener('click',()=>{treeButtons.forEach(b=>collapsed.add(b.dataset.pivotNode));updateTree();});
 $('pivot-reset').addEventListener('click',()=>{config=structuredClone(defaults);totalChoices.rows.clear();totalChoices.columns.clear();render();});render();
})();
