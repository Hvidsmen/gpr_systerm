(()=>{
 'use strict';const root=document.querySelector('[data-month-grid]');if(!root)return;
 const form=document.getElementById('allocation-editor'),status=root.querySelector('[data-grid-status]'),inputs=[...root.querySelectorAll('[data-grid-input]')],entries=[...root.querySelectorAll('[data-grid-entry]')];let saving=false;
 const dirty=()=>inputs.filter(input=>input.value!==input.dataset.initial),numeric=value=>Number(String(value).replace(/\s/g,'').replace(',','.'));
 function say(text,error=false){status.textContent=text;status.classList.toggle('text-danger',error);const header=document.querySelector('[data-plan-save-status]');if(header){header.textContent=text;header.dataset.state=error?'error':'saved';}}
 function changed(input){input.toggleAttribute('data-dirty',input.value!==input.dataset.initial);if(dirty().length)window.gprSaveUI?.markPlanDirty();else form.dataset.dirty='false';status.textContent=dirty().length?'Есть изменения по месяцам':'Сохранено';recalculate(input.closest('[data-grid-entry]'));}
 function recalculate(entry){const norms=JSON.parse(entry.dataset.norms||'{}');if(!Object.keys(norms).length)return;const total=entry.querySelector('[data-field=ready]');if(!total)return;
  total.querySelectorAll('[data-column]').forEach(cell=>{const index=cell.dataset.column;let minimum=Infinity;
   for(const [id,norm] of Object.entries(norms)){const source=entry.querySelector(`[data-field="item:${id}"] [data-column="${index}"]`),value=source?numeric(source.querySelector('input')?.value??source.dataset.value):0;minimum=Math.min(minimum,(Number.isFinite(value)?value:0)/Number(norm));}
   const value=entry.dataset.fractional==='1'?Math.floor(minimum*1000+1e-8)/1000:Math.floor(minimum+1e-8);cell.querySelector('[data-grid-number]').textContent=Number.isFinite(value)?String(value):'0';
  });
 }
 inputs.forEach(input=>input.addEventListener('input',()=>changed(input)));
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(saving)return;
  const changedInputs=dirty();if(!changedInputs.length){say('Все изменения сохранены');return;}
  const invalid=changedInputs.find(input=>!input.value.trim()||!input.checkValidity());if(invalid){say('Проверьте число: требуется значение не меньше 0. Для нуля введите 0.',true);invalid.focus();invalid.reportValidity();return;}
  const changes=changedInputs.map(input=>{const cell=input.closest('td'),entry=input.closest('[data-grid-entry]');return {kind:entry.dataset.kind,key:entry.dataset.key,source:entry.dataset.source,month:cell.dataset.month,id:cell.dataset.id,revision:cell.dataset.revision,field:input.closest('[data-grid-measure]').dataset.field,value:input.value};});
  saving=true;inputs.forEach(input=>input.disabled=true);document.querySelectorAll('button[form="allocation-editor"]').forEach(button=>button.disabled=true);say('Сохранение месяцев…');
  try{const response=await fetch(root.dataset.url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({changes})}),result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить план.');
   form.dataset.dirty='false';changedInputs.forEach(input=>input.dataset.initial=input.value);sessionStorage.setItem('gpr-month-grid:'+location.pathname,JSON.stringify({left:root.querySelector('[data-grid-scroll]').scrollLeft,top:scrollY}));saving=false;location.reload();
  }catch(error){say(error.message,true);}finally{saving=false;inputs.forEach(input=>input.disabled=false);document.querySelectorAll('button[form="allocation-editor"]').forEach(button=>button.disabled=false);}
 },true);
 root.addEventListener('paste',event=>{
  const input=event.target.closest('[data-grid-input]');if(!input)return;const text=event.clipboardData.getData('text/plain');if(!text.includes('\t')&&!/[\r\n]/.test(text)){const value=text.trim().replace(/\s/g,'').replace(',','.');if(value&&Number.isFinite(Number(value))){event.preventDefault();input.value=value;changed(input);}return;}
  event.preventDefault();const block=text.replace(/\r/g,'').replace(/\n$/,'').split('\n').map(line=>line.split('\t')),rows=[...root.querySelectorAll('[data-grid-measure]')].filter(row=>!row.closest('tbody').hidden),start=rows.indexOf(input.closest('tr')),column=Number(input.closest('td').dataset.column),updates=[];
  for(let y=0;y<block.length;y++)for(let x=0;x<block[y].length;x++){if(!block[y][x].trim())continue;const target=rows[start+y]?.querySelector(`[data-column="${column+x}"] input`),value=block[y][x].trim().replace(/\s/g,'').replace(',','.');if(!target||!Number.isFinite(Number(value))||Number(value)<0||target.step==='1'&&!Number.isInteger(Number(value))){say('Диапазон содержит недоступную ячейку или неверное число. Вставка отменена.',true);return;}updates.push([target,value]);}
  updates.forEach(([target,value])=>{target.value=value;changed(target);});say('Вставлено ячеек: '+updates.length+'. Нажмите «Сохранить».');
 });
 function filter(){const q=root.querySelector('[data-grid-search]').value.toLocaleLowerCase(),kind=root.querySelector('[data-grid-filter]').value;
  entries.forEach(entry=>entry.hidden=Boolean(kind&&entry.dataset.section!==kind)||!entry.dataset.search.toLocaleLowerCase().includes(q));
  root.querySelectorAll('[data-grid-group-heading]').forEach(group=>group.hidden=!entries.some(entry=>!entry.hidden&&entry.dataset.group===group.dataset.gridGroupHeading));
  root.querySelectorAll('[data-grid-section-heading]').forEach(section=>section.hidden=!entries.some(entry=>!entry.hidden&&entry.dataset.section===section.dataset.gridSectionHeading));
  root.querySelectorAll('[data-grid-empty-section]').forEach(section=>section.hidden=Boolean(q)||Boolean(kind&&kind!==section.dataset.gridEmptySection));
  root.querySelector('[data-grid-no-matches]').hidden=!entries.length||entries.some(entry=>!entry.hidden);
 }
 root.querySelector('[data-grid-search]').addEventListener('input',filter);root.querySelector('[data-grid-filter]').addEventListener('change',filter);
 root.querySelector('[data-grid-copy]').addEventListener('click',async()=>{const rows=[['Работа / ресурс','Показатель',...[...root.querySelectorAll('[data-grid-month]')].map(cell=>cell.innerText)]];
  entries.filter(entry=>!entry.hidden).forEach(entry=>entry.querySelectorAll('[data-grid-measure]').forEach(row=>rows.push([entry.querySelector('.month-grid-name').childNodes[0].textContent.trim(),row.querySelector('.month-grid-measure').textContent.trim(),...[...row.querySelectorAll('[data-column]')].map(cell=>cell.querySelector('input')?.value??cell.innerText.trim())])));
  try{await navigator.clipboard.writeText(rows.map(row=>row.map(value=>value.replace(/[\t\r\n]+/g,' ')).join('\t')).join('\n'));say('Таблица скопирована для Excel.');}catch{say('Браузер не разрешил копирование. Используйте экспорт Excel.',true);}
 });
 root.querySelectorAll('[data-grid-open]').forEach(button=>button.addEventListener('click',()=>{if(dirty().length){say('Сначала сохраните изменения месяцев.',true);return;}root.querySelector(`[data-grid-dialog="${button.dataset.gridOpen}"]`).showModal();}));
 root.querySelectorAll('[data-grid-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));root.querySelectorAll('dialog[data-open]').forEach(dialog=>dialog.showModal());
 const resource=root.querySelector('[data-grid-dialog=resource]');if(resource){const kind=resource.querySelector('[name=kind]');kind.querySelector('[value=work]')?.remove();function show(){const fields={labor:['kind','brigade','rate'],equipment:['kind','equipment','rate'],fuel:['kind','fuel','rate']}[kind.value]||[];resource.querySelectorAll('[data-grid-add-field]').forEach(field=>field.hidden=!fields.includes(field.dataset.gridAddField));}kind.addEventListener('change',show);show();}
 const top=root.querySelector('[data-grid-top-scroll]'),view=root.querySelector('[data-grid-scroll]');function sync(a,b){if(Math.abs(a.scrollLeft-b.scrollLeft)>.5)b.scrollLeft=a.scrollLeft;}top.addEventListener('scroll',()=>sync(top,view));view.addEventListener('scroll',()=>sync(view,top));
 function size(){top.hidden=view.scrollWidth<=view.clientWidth+1;top.firstElementChild.style.width=view.scrollWidth+'px';sync(view,top);}if(window.ResizeObserver){const observer=new ResizeObserver(size);observer.observe(view);observer.observe(view.querySelector('table'));}size();
 try{const key='gpr-month-grid:'+location.pathname,saved=JSON.parse(sessionStorage.getItem(key)||'null');sessionStorage.removeItem(key);if(saved)requestAnimationFrame(()=>{view.scrollLeft=saved.left;scrollTo(0,saved.top);});}catch{}
 window.addEventListener('beforeunload',event=>{if(saving||dirty().length){event.preventDefault();event.returnValue='';}});
})();
