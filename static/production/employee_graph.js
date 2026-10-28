(() => {
 'use strict';
 const form=document.querySelector('[data-production-form]'),graph=document.querySelector('[data-employee-graph]');if(!form||!graph)return;
 const catalogue=JSON.parse(document.getElementById('production-work-choices').textContent);let saved=JSON.parse(document.getElementById('production-graph-saved').textContent);const labels=new Map(catalogue.map(w=>[w.token,w]));
 const workHost=graph.querySelector('[data-graph-works]'),peopleHost=graph.querySelector('[data-graph-people]'),svg=graph.querySelector('svg'),canvas=graph.querySelector('.employee-graph-canvas'),message=graph.querySelector('[data-graph-message]');
 const key=(w,i)=>i?`item:${w}:${i}`:`work:${w}`,field=(row,name)=>row.querySelector(`[name$="-${name}"]`),personRows=()=>[...form.querySelectorAll('[data-production-person]')];
 let active=null,step=1,scheduled=false,workCards=new Map(),personCards=new Map();
 function showStep(next){step=Math.max(1,Math.min(4,next));form.querySelectorAll('[data-entry-step]').forEach(p=>{p.hidden=Number(p.dataset.entryStep)!==step;});form.querySelectorAll('[data-step]').forEach(b=>{b.setAttribute('aria-current',Number(b.dataset.step)===step?'step':'false');});const nextButton=form.querySelector('[data-step-next]'),confirmButton=form.querySelector('[data-confirm-production]');if(nextButton)nextButton.hidden=step===4;if(confirmButton)confirmButton.hidden=step!==4;if(step===3)render();form.dispatchEvent(new CustomEvent('production-step-change',{detail:step}));}
 form.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>showStep(Number(b.dataset.step))));form.querySelector('[data-step-next]')?.addEventListener('click',()=>showStep(step+1));
 form.addEventListener('invalid',event=>{const panel=event.target.closest('[data-entry-step]');if(panel)showStep(Number(panel.dataset.entryStep));},true);
 function targets(row){return [...row.querySelectorAll('[name$="-work_targets"]')].map(i=>i.value).filter(Boolean);}
 function editable(row){return form.dataset.readonly!=='1'&&field(row,'person_editing')?.value!=='0';}
 function update(row,values,mode){if(!editable(row)){message.textContent='Сначала возьмите сотрудника на шаге «Люди»: его связи защищены резервом.';return;}
  const name=field(row,'work_mode').name.replace(/work_mode$/,'work_targets');row.querySelectorAll('[name$="-work_targets"]').forEach(i=>i.remove());
  for(const value of values){const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;row.append(input);}
  field(row,'work_mode').value=mode;row.dataset.personDirty='1';row.dispatchEvent(new Event('change',{bubbles:true}));render();
 }
 function connect(token,row){const selected=targets(row),remove=selected.includes(token);update(row,remove?selected.filter(x=>x!==token):[...selected,token],remove&&selected.length===1?'pending':'works');active=null;}
 function button(label,action){const b=document.createElement('button');b.type='button';b.className='btn btn-sm btn-outline-primary';b.textContent=label;b.addEventListener('click',event=>{event.stopPropagation();action();});return b;}
 function render(){
  const available=new Set(saved.map(r=>key(r.project_work_id,r.work_item_id)));
  form.querySelectorAll('[data-work-fact-row]').forEach(row=>{const w=field(row,'project_work')?.value;if(!w)return;const token=key(w,field(row,'work_item')?.value);if(field(row,'DELETE')?.checked)available.delete(token);else if(field(row,'actual_quantity')?.value.trim())available.add(token);else if(row.dataset.entryDirty)available.delete(token);});
  const works=catalogue.filter(w=>available.has(w.token)),people=personRows().filter(r=>field(r,'employee')?.value&&!field(r,'DELETE')?.checked).map(row=>({id:field(row,'work_mode').name,row,workTokens:field(row,'work_mode').value==='works'?targets(row):[]}));
  const layout=window.resourceGraphLayout.arrange(works,people),orderedWorks=[...layout.components.flatMap(c=>c.works),...layout.unlinkedWorks],orderedPeople=[...layout.components.flatMap(c=>c.resources),...layout.hiddenResources,...layout.unlinkedResources];
  workHost.replaceChildren();peopleHost.replaceChildren();workCards=new Map();personCards=new Map();
  for(const work of orderedWorks){const card=document.createElement('div');card.className='employee-graph-card graph-work';card.dataset.token=work.token;card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-label',work.label);card.textContent=work.label;
   const port=button('●',()=>selectWork(work.token));port.classList.add('graph-port');port.setAttribute('aria-label','Связать: '+work.label);port.draggable=true;port.addEventListener('dragstart',e=>{active=work.token;e.dataTransfer.setData('text/plain',work.token);});card.append(port);
   card.addEventListener('click',()=>selectWork(work.token));card.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectWork(work.token);}});workHost.append(card);workCards.set(work.token,card);
  }
  let previousGroup=null;
  for(const person of orderedPeople){const row=person.row,mode=field(row,'work_mode').value,group=person.workTokens.length?'Связаны с работами':mode==='general'?'Работали без привязки':'Связи пока не проверены';
   if(group!==previousGroup){const h=document.createElement('p');h.className='small text-muted mt-3 mb-2';h.textContent=group;peopleHost.append(h);previousGroup=group;}
   const card=document.createElement('div');card.className='employee-graph-card graph-person';card.dataset.person=person.id;const name=document.createElement('strong');name.textContent=(field(row,'employee').selectedOptions[0]?.textContent||'Сотрудник').split(' · ')[0];card.append(name);
   const meta=document.createElement('p');meta.className='small text-muted my-2';meta.textContent=`Смена ${field(row,'shift').value} · `+(person.workTokens.length?`Связей: ${person.workTokens.length}`:group);card.append(meta);
   const port=button('●',()=>{if(active)connect(active,row);else{message.textContent='Выберите работу слева, затем нажмите точку сотрудника.';highlight(null,person.id);}});port.classList.add('graph-port');port.setAttribute('aria-label','Связать сотрудника '+name.textContent);card.append(port);
   const actions=document.createElement('div');actions.className='d-flex gap-2 flex-wrap';actions.append(button('Выбрать работы',()=>{if(!editable(row)){message.textContent='Возьмите сотрудника на шаге «Люди».';return;}row.querySelector('[data-person-pick]').click();}),button(mode==='general'?'Назначить работы':'Без привязки',()=>update(row,[],mode==='general'?'pending':'general')));card.append(actions);
   if(!editable(row)){const note=document.createElement('p');note.className='small text-muted mb-0';note.textContent='Сначала возьмите сотрудника на шаге «Люди»';card.append(note);}
   card.addEventListener('dragover',e=>{if(active)e.preventDefault();});card.addEventListener('drop',e=>{e.preventDefault();if(active&&available.has(active))connect(active,row);});card.addEventListener('click',e=>{if(!e.target.closest('button')){if(active)connect(active,row);else highlight(null,person.id);}});
   peopleHost.append(card);personCards.set(person.id,{card,person});
  }
  if(!works.length)workHost.textContent='На шаге «Работы» введите объёмы за день.';if(!people.length)peopleHost.textContent='На шаге «Люди» добавьте сотрудников.';
  filter();requestAnimationFrame(draw);
 }
 function selectWork(token){active=active===token?null:token;message.textContent=active?'Теперь выберите сотрудника справа. Повторное соединение удалит связь.':'';highlight(active,null);}
 function highlight(work,person){for(const [key,card] of workCards)card.classList.toggle('selected',key===work);for(const [id,{card}] of personCards)card.classList.toggle('selected',id===person);svg.querySelectorAll('path').forEach(p=>p.classList.toggle('selected',p.dataset.work===work||p.dataset.person===person));}
 function draw(){if(graph.hidden)return;const rect=canvas.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${rect.width} ${canvas.offsetHeight}`);svg.replaceChildren();for(const [id,{card,person}] of personCards){if(card.hidden)continue;for(const token of person.workTokens){const work=workCards.get(token);if(!work||work.hidden)continue;const a=work.querySelector('.graph-port').getBoundingClientRect(),b=card.querySelector('.graph-port').getBoundingClientRect(),x1=a.x+a.width/2-rect.x,y1=a.y+a.height/2-rect.y,x2=b.x+b.width/2-rect.x,y2=b.y+b.height/2-rect.y;const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',`M${x1},${y1} C${x1+(x2-x1)*.5},${y1} ${x2-(x2-x1)*.5},${y2} ${x2},${y2}`);path.dataset.work=token;path.dataset.person=id;const title=document.createElementNS(path.namespaceURI,'title');title.textContent=(labels.get(token)?.label||token)+' → '+field(person.row,'employee').selectedOptions[0].textContent;path.append(title);svg.append(path);}}highlight(active,null);}
 function filter(){const a=graph.querySelector('[data-graph-work-search]').value.toLocaleLowerCase('ru'),b=graph.querySelector('[data-graph-person-search]').value.toLocaleLowerCase('ru');for(const card of workCards.values())card.hidden=!card.textContent.toLocaleLowerCase('ru').includes(a);for(const {card} of personCards.values())card.hidden=!card.textContent.toLocaleLowerCase('ru').includes(b);draw();}
 graph.querySelectorAll('input[type=search]').forEach(i=>i.addEventListener('input',filter));
 form.addEventListener('production-report-update',event=>{saved=event.detail.works;document.getElementById('production-graph-saved').textContent=JSON.stringify(saved);if(step===3)render();});
 form.addEventListener('change',()=>{if(!scheduled){scheduled=true;requestAnimationFrame(()=>{scheduled=false;if(step===3)render();});}});form.addEventListener('production-claims-change',()=>{if(step===3)render();});new ResizeObserver(draw).observe(canvas);
 const errors=form.querySelector('[data-entry-step] .errorlist');showStep(errors?Number(errors.closest('[data-entry-step]').dataset.entryStep):Number(form.dataset.initialStep||1));
})();
