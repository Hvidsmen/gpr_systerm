(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]'),graph=document.querySelector('[data-employee-graph]');if(!form||!graph)return;
 const employeeLocations=JSON.parse(document.getElementById('production-employee-locations')?.textContent||'{}');let savedPeople=JSON.parse(document.getElementById('production-report-people')?.textContent||'[]');
 const catalogue=JSON.parse(document.getElementById('production-work-choices').textContent);let saved=JSON.parse(document.getElementById('production-graph-saved').textContent);const labels=new Map(catalogue.map(w=>[w.token,w]));
 const workHost=graph.querySelector('[data-graph-works]'),peopleHost=graph.querySelector('[data-graph-people]'),svg=graph.querySelector('svg'),canvas=graph.querySelector('.employee-graph-canvas'),message=graph.querySelector('[data-graph-message]');
 const key=(w,i)=>i?`item:${w}:${i}`:`work:${w}`,factToken=f=>f.operational_name?`operational:${f.id}`:key(f.project_work_id,f.work_item_id),field=(row,name)=>row.querySelector(`[name$="-${name}"]`),personRows=()=>[...form.querySelectorAll('[data-production-person]')];
 let active=null,activePerson=null,activePersonRow=null,step=1,scheduled=false,workCards=new Map(),personCards=new Map();
 function showStep(next){step=Math.max(1,Math.min(5,next));const currentUrl=new URL(location.href);currentUrl.searchParams.set('entry_step',step);history.replaceState(null,'',currentUrl);document.querySelectorAll('[data-entry-step]').forEach(p=>{p.hidden=Number(p.dataset.entryStep)!==step;});form.querySelectorAll('[data-step]').forEach(b=>{b.setAttribute('aria-current',Number(b.dataset.step)===step?'step':'false');});const nextButton=form.querySelector('[data-step-next]'),confirmButton=form.querySelector('[data-confirm-production]');const previousButton=form.querySelector('[data-step-previous]');if(previousButton)previousButton.hidden=step===1;if(nextButton){nextButton.hidden=step===5;nextButton.textContent=({1:'К сотрудникам →',2:'Назначить работы →',3:'К сводному отчёту →',4:'К плану / факту СМГ →'})[step]||'Далее';}if(confirmButton)confirmButton.hidden=step!==4;if(step===3)render();form.dispatchEvent(new CustomEvent('production-step-change',{detail:step}));}
 form.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>showStep(Number(b.dataset.step))));form.querySelector('[data-step-next]')?.addEventListener('click',()=>showStep(step+1));form.querySelector('[data-step-previous]')?.addEventListener('click',()=>showStep(step-1));
 form.addEventListener('invalid',event=>{const panel=event.target.closest('[data-entry-step]');if(panel)showStep(Number(panel.dataset.entryStep));},true);
 function targets(row){return [...row.querySelectorAll('[name$="-work_targets"]')].map(i=>i.value).filter(Boolean);}
 function editable(row){return form.dataset.readonly!=='1';}
 function applyConfirmed(row,op){
  const mode=field(row,'work_mode');
  if(op.action==='mode'){row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());mode.value=op.mode==='general'?'general':'pending';}
  else{const fact=saved.find(w=>Number(w.id)===Number(op.fact)),target=fact&&factToken(fact);if(!target)return;const existing=[...row.querySelectorAll('[name$="-work_targets"]')];if(op.action==='remove')existing.filter(i=>i.value===target).forEach(i=>i.remove());else if(!existing.some(i=>i.value===target)){const input=document.createElement('input');input.type='hidden';input.name=mode.name.replace(/work_mode$/,'work_targets');input.value=target;row.append(input);}mode.value=targets(row).length?'works':'pending';}
  structureStamp='';render();form.productionRefresh?.();
 }
 async function operation(row,op){
  if(!editable(row))return;
  try{await form.productionChangeLinks(row,op);message.textContent='Сохраняем связь…';if(form.productionLinks&&!await form.productionLinks.flush())throw new Error('Связь не отправлена. Проверьте сообщение над вкладками и нажмите «Отправить сейчас».');applyConfirmed(row,op);message.textContent='Связи сохранены';}
  catch(error){message.textContent=error.message;}
 }
 function update(row,values,mode){operation(row,{action:'mode',mode});}
 function connect(token,row){
  const fact=saved.find(w=>factToken(w)===token);
  if(!fact){message.textContent='Дождитесь автоматического сохранения объёма работы.';return;}
  const remove=targets(row).includes(token);if(!remove&&Number(fact.actual_quantity)<=0){message.textContent='У работы нулевой факт. Сначала введите выполненный объём больше нуля.';return;}operation(row,{action:remove?'remove':'add',fact:fact.id});highlight(active,activePerson);
 }
 function button(label,action){const b=document.createElement('button');b.type='button';b.className='btn btn-sm btn-outline-primary';b.textContent=label;b.addEventListener('click',event=>{event.stopPropagation();action();});return b;}
 let structureStamp='',drawPending=false;
 function render(){
  const previous=new Map([...workCards].map(([id,card])=>['w:'+id,card.getBoundingClientRect()]));for(const [id,{card}] of personCards)previous.set('p:'+id,card.getBoundingClientRect());
  for(const fact of saved.filter(row=>row.operational_name)){const token=factToken(fact);if(!labels.has(token)){const work={token,work:fact.project_work_id,item:null,label:`Прочее → ${fact.operational_name}`,title:fact.operational_name,unit:fact.operational_unit,kind:'OPERATIONAL',object:'Прочее',package:'Прочее'};catalogue.push(work);labels.set(token,work);}}
  const available=new Set(saved.map(factToken));
  form.querySelectorAll('[data-work-fact-row]').forEach(row=>{const w=field(row,'project_work')?.value;if(!w)return;const token=field(row,'operational_name')?.value?(row.dataset.factId?`operational:${row.dataset.factId}`:''):key(w,field(row,'work_item')?.value);if(!token)return;if(field(row,'DELETE')?.checked)available.delete(token);else if(field(row,'actual_quantity')?.value.trim())available.add(token);else if(row.dataset.entryDirty)available.delete(token);});
  const works=catalogue.filter(w=>available.has(w.token)),people=personRows().filter(r=>field(r,'employee')?.value&&!field(r,'DELETE')?.checked).map(row=>({id:field(row,'work_mode').name,row,workTokens:field(row,'work_mode').value==='works'?targets(row):[]}));
  const stamp=JSON.stringify([works.map(w=>[w.token,saved.find(s=>factToken(s)===w.token)?.actual_quantity]),people.map(p=>[p.id,field(p.row,'employee').value,field(p.row,'planning_position')?.value,field(p.row,'shift').value])]);
  if(stamp===structureStamp&&personCards.size===people.length){
   for(const person of people){const current=personCards.get(person.id);if(!current)continue;current.person=person;const mode=field(person.row,'work_mode').value,pending=Number(person.row.dataset.pendingLinks||0);
    current.card.querySelector('.graph-person-meta').textContent=(field(person.row,'shift').selectedOptions?.[0]?.textContent||'')+' · '+(person.workTokens.length?'Связей: '+person.workTokens.length:mode==='general'?'Работали без привязки':'Связи пока не проверены')+(pending?' · в очереди: '+pending:'');
    const modeButton=current.card.querySelector('[data-graph-mode]');if(modeButton)modeButton.textContent=mode==='general'?'Назначить работы':'Без привязки';current.card.classList.toggle('graph-pending',pending>0);
   }
   filter();draw();return;
  }
  structureStamp=stamp;
  const layout=window.resourceGraphLayout.arrange(works,people),orderedWorks=[...layout.components.flatMap(c=>c.works),...layout.unlinkedWorks],orderedPeople=[...layout.components.flatMap(c=>c.resources),...layout.hiddenResources,...layout.unlinkedResources];
  workHost.replaceChildren();peopleHost.replaceChildren();workCards=new Map();personCards=new Map();
  for(const work of orderedWorks){const card=document.createElement('div');card.className='employee-graph-card graph-work';card.dataset.token=work.token;card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-label',work.label);card.title=work.label;const kind=document.createElement('small');kind.className='graph-work-kind';kind.textContent=work.kind==='OPERATIONAL'?'Прочая работа':work.kind==='COMPOSITE'?'В составе комплекса':'Самостоятельная работа';const path=document.createElement('span');path.className='graph-work-path';path.textContent=work.label.slice(0,Math.max(0,work.label.lastIndexOf(' → ')));const title=document.createElement('strong');title.textContent=work.title||work.label;card.append(kind,path,title);const value=saved.find(w=>factToken(w)===work.token);if(value&&Number(value.actual_quantity)===0){const zero=document.createElement('small');zero.className='text-warning-emphasis';zero.textContent='Объём работы — 0. Для назначения внесите выполненный объём.';card.append(zero);}
   const port=button('●',()=>selectWork(work.token));port.classList.add('graph-port');port.setAttribute('aria-label','Связать: '+work.label);port.draggable=true;port.addEventListener('dragstart',e=>{active=work.token;e.dataTransfer.setData('text/plain',work.token);});card.append(port);
   card.addEventListener('dragover',e=>{if(activePersonRow)e.preventDefault();});card.addEventListener('drop',e=>{e.preventDefault();if(activePersonRow)connect(work.token,activePersonRow);});card.addEventListener('click',()=>selectWork(work.token));card.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectWork(work.token);}});workHost.append(card);workCards.set(work.token,card);
  }
  let previousGroup=null;
  for(const person of orderedPeople){const row=person.row,mode=field(row,'work_mode').value,group=person.workTokens.length?'Связаны с работами':mode==='general'?'Работали без привязки':'Связи пока не проверены';
   const card=document.createElement('div');card.className='employee-graph-card graph-person';card.dataset.person=person.id;const name=document.createElement('strong');name.textContent=(field(row,'employee').selectedOptions[0]?.textContent||'Сотрудник').split(' · ')[0];card.append(name);
   const employeeId=field(row,'employee').value,planning=field(row,'planning_position'),plannedRole=planning?.value?planning.selectedOptions[0]?.textContent:'',snapshot=savedPeople.find(item=>String(item.id)===String(field(row,'id')?.value)),staffRole=snapshot?.staff_position||employeeLocations[employeeId]?.position_label||'';
   const position=document.createElement('p');position.className='small graph-person-role';position.dataset.graphPersonPosition='';position.textContent=(plannedRole||staffRole||'Должность не указана');card.append(position);
   if(plannedRole&&staffRole&&plannedRole.trim().toLocaleLowerCase('ru')!==staffRole.trim().toLocaleLowerCase('ru')){const staff=document.createElement('p');staff.className='small text-muted mb-2';staff.dataset.graphStaffPosition='';staff.textContent='Штатная: '+staffRole;card.append(staff);}
   const meta=document.createElement('p');meta.className='small text-muted graph-person-meta';meta.textContent=`${field(row,'shift').selectedOptions?.[0]?.textContent||('Смена '+field(row,'shift').value)} · `+(person.workTokens.length?`Связей: ${person.workTokens.length}`:group);card.append(meta);
   const port=button('●',()=>selectPerson(person.id,row));port.classList.add('graph-port');port.setAttribute('aria-label','Связать сотрудника '+name.textContent);port.draggable=true;port.addEventListener('dragstart',e=>{active=null;activePerson=person.id;activePersonRow=row;e.dataTransfer.setData('text/plain',person.id);highlight(null,activePerson);});card.append(port);
   const actions=document.createElement('div');actions.className='d-flex gap-2 flex-wrap';actions.append(button('Выбрать работы',()=>{if(!editable(row))return;row.querySelector('[data-person-pick]').click();}),button(mode==='general'?'Назначить работы':'Без привязки',()=>update(row,[],field(row,'work_mode').value==='general'?'pending':'general')));actions.lastElementChild.dataset.graphMode='';card.append(actions);
   card.addEventListener('dragover',e=>{if(active)e.preventDefault();});card.addEventListener('drop',e=>{e.preventDefault();if(active&&available.has(active))connect(active,row);});card.addEventListener('click',e=>{if(!e.target.closest('button'))selectPerson(person.id,row);});
   peopleHost.append(card);personCards.set(person.id,{card,person});
  }
  if(!works.length)workHost.textContent='На шаге «Работы» введите объёмы за день.';if(!people.length)peopleHost.textContent='На шаге «Люди» добавьте сотрудников.';
  if(form.productionFilters)form.productionFilters.syncGraph();
  filter();
  // Both sides of each connected component share a height and centre line.
  for(const component of layout.components){
   const left=document.createElement('div'),right=document.createElement('div');left.className=right.className='graph-component';
   for(const w of component.works)left.append(workCards.get(w.token));for(const p of component.resources)right.append(personCards.get(p.id).card);
   workHost.append(left);peopleHost.append(right);
   const height=Math.max(left.offsetHeight,right.offsetHeight);left.style.minHeight=right.style.minHeight=height+'px';
  }
  // Place isolated cards after connected components.
  for(const w of layout.unlinkedWorks)workHost.append(workCards.get(w.token));for(const p of [...layout.hiddenResources,...layout.unlinkedResources])peopleHost.append(personCards.get(p.id).card);
  draw();
 }
 function selectWork(token){
  if(activePersonRow){connect(token,activePersonRow);return;}
  active=active===token?null:token;message.textContent=active?'Теперь выберите сотрудника справа. Повторное соединение удалит связь.':'';highlight(active,null);
 }
 function selectPerson(id,row){
  if(active){connect(active,row);return;}
  if(activePerson===id){activePerson=null;activePersonRow=null;}else{activePerson=id;activePersonRow=row;}message.textContent=activePerson?'Теперь выберите работу слева. Можно связать сразу с несколькими работами.':'';highlight(null,activePerson);
 }
 function highlight(work,person){for(const [key,card] of workCards)card.classList.toggle('selected',key===work);for(const [id,{card}] of personCards)card.classList.toggle('selected',id===person);svg.querySelectorAll('path').forEach(p=>p.classList.toggle('selected',p.dataset.work===work||p.dataset.person===person));}
 function draw(){if(drawPending||graph.hidden)return;drawPending=true;requestAnimationFrame(()=>{drawPending=false;drawNow();});}
 function drawNow(){if(graph.hidden)return;svg.replaceChildren();if(!graph.classList.contains('graph-lines-visible'))return;const rect=canvas.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${rect.width} ${canvas.offsetHeight}`);const workPorts=new Map([...workCards].filter(([,card])=>!card.hidden).map(([id,card])=>[id,card.querySelector('.graph-port').getBoundingClientRect()]));const personPorts=new Map([...personCards].filter(([,value])=>!value.card.hidden).map(([id,value])=>[id,value.card.querySelector('.graph-port').getBoundingClientRect()]));for(const [id,{card,person}] of personCards){if(card.hidden)continue;for(const token of person.workTokens){const work=workCards.get(token);if(!work||work.hidden)continue;const a=workPorts.get(token),b=personPorts.get(id),x1=a.x+a.width/2-rect.x,y1=a.y+a.height/2-rect.y,x2=b.x+b.width/2-rect.x,y2=b.y+b.height/2-rect.y;const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',`M${x1},${y1} C${x1+(x2-x1)*.5},${y1} ${x2-(x2-x1)*.5},${y2} ${x2},${y2}`);path.dataset.work=token;path.dataset.person=id;const title=document.createElementNS(path.namespaceURI,'title');title.textContent=(labels.get(token)?.label||token)+' → '+field(person.row,'employee').selectedOptions[0].textContent;path.append(title);svg.append(path);}}highlight(active,activePerson);}
 function filter(){const a=graph.querySelector('[data-graph-work-search]').value.toLocaleLowerCase('ru'),b=graph.querySelector('[data-graph-person-search]').value.toLocaleLowerCase('ru');for(const card of workCards.values())card.hidden=!card.textContent.toLocaleLowerCase('ru').includes(a)||Boolean(form.productionFilters&&!form.productionFilters.workMatches(card.dataset.token));for(const {card,person} of personCards.values())card.hidden=!card.textContent.toLocaleLowerCase('ru').includes(b)||Boolean(form.productionFilters&&!form.productionFilters.personMatches(person.row));draw();}
 let filterPending=false;function scheduleFilter(){if(filterPending)return;filterPending=true;requestAnimationFrame(()=>{filterPending=false;filter();});}
 graph.querySelectorAll('input[type=search]').forEach(i=>i.addEventListener('input',scheduleFilter));
 form.addEventListener('production-report-update',event=>{saved=event.detail.works;savedPeople=event.detail.people;document.getElementById('production-graph-saved').textContent=JSON.stringify(saved);if(step===3)render();});
 form.addEventListener('production-work-created',event=>{catalogue.push(event.detail);labels.set(event.detail.token,event.detail);structureStamp='';if(step===3)render();});
 form.addEventListener('change',()=>{if(!scheduled){scheduled=true;requestAnimationFrame(()=>{scheduled=false;if(step===3)render();});}});form.addEventListener('production-filter-change',()=>{if(step===3)render();});new ResizeObserver(draw).observe(canvas);
 const errors=form.querySelector('[data-entry-step] .errorlist');showStep(errors?Number(errors.closest('[data-entry-step]').dataset.entryStep):Number(form.dataset.initialStep||1));
})();
