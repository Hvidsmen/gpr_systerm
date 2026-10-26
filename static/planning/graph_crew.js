(() => {
 'use strict';
 const dialog=document.querySelector('[data-graph-crew-dialog]');if(!dialog)return;
 const api=window.gprResourceEditor,cfg=JSON.parse(document.getElementById('resource-graph-data').textContent),q=s=>dialog.querySelector(s);
 const host=q('[data-crew-rows]'),form=q('[data-graph-crew-form]');
 let info=null,assignment=null,busy=false,dirty=false,opener=null;
 function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
 function status(state,text){const node=document.querySelector('[data-graph-status]');node.dataset.state=state;node.textContent=text;}
 function error(message){q('[data-crew-error]').hidden=!message;q('[data-crew-error]').textContent=message;}
 function selected(){return [...host.children].filter(row=>row.querySelector('[data-crew-check]').checked);}
 function update(){
  let total=0;
  for(const row of host.children){
   const checked=row.querySelector('[data-crew-check]').checked,count=row.querySelector('[data-crew-count]');row.querySelector('.rg-crew-fields').hidden=!checked;
   count.disabled=!checked;count.required=checked;
   const people=Number(count.value)*info.count;if(checked)total+=people;
   row.querySelector('[data-crew-quantity]').textContent=people+' чел. в экипаже этой техники';
  }
  q('[data-crew-total]').textContent='Экипаж: '+total+' чел. на смену';
 }
 function filter(){const words=q('[data-crew-search]').value.toLocaleLowerCase('ru').replace(/ё/g,'е').split(/\s+/).filter(Boolean);for(const row of host.children)row.hidden=!words.every(w=>row.dataset.search.includes(w));}
 async function responseJSON(response){if(!(response.headers.get('content-type')||'').includes('application/json'))throw new Error('Проверьте доступ к плану и обновите страницу.');const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить экипаж.');return result;}
 document.addEventListener('gpr-open-crew',async event=>{
  if(busy||dialog.open)return;
  assignment=event.detail.assignment;opener=document.activeElement;busy=true;dirty=false;info=null;host.replaceChildren();error('');q('#rg-crew-title').textContent=assignment.label;q('[data-crew-save]').disabled=true;q('[data-crew-meta]').textContent='Загрузка экипажа…';q('[data-crew-search]').value='';q('[data-crew-total]').textContent='';dialog.showModal();
  try{
   const url=new URL(cfg.crew_url,location.href);url.searchParams.set('assignment_id',assignment.id);
   info=await responseJSON(await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'}}));
   q('#rg-crew-title').textContent=info.label;q('[data-crew-meta]').textContent=info.count+' ед. техники · численность задаётся на одну единицу';
   const current=new Map(info.crew.map(row=>[row.position_id,row]));
   for(const position of info.positions){
    const previous=current.get(position.id),row=el('section','rg-crew-position');row.dataset.position=position.id;row.dataset.search=position.name.toLocaleLowerCase('ru').replace(/ё/g,'е');
    const label=el('label','rg-crew-choice'),check=el('input');check.type='checkbox';check.dataset.crewCheck='';check.checked=Boolean(previous);label.append(check,document.createTextNode(position.name));row.append(label);
    const fields=el('div','rg-crew-fields'),countLabel=el('label','', 'Человек на одну машину'),count=el('input');count.type='number';count.min='1';count.max='1000';count.step='1';count.value=previous?.count||1;count.dataset.crewCount='';count.setAttribute('aria-label','Человек на одну машину: '+position.name);countLabel.append(count);fields.append(countLabel);
    const replacementLabel=el('label','rg-crew-choice'),replacement=el('input');replacement.type='checkbox';replacement.checked=Boolean(previous?.is_replacement);replacement.dataset.crewReplacement='';replacementLabel.append(replacement,document.createTextNode('Замещение *'));fields.append(replacementLabel);
    const sourceLabel=el('label','', 'Учёт людей'),source=el('select');source.dataset.nativeSelect='';source.dataset.crewSource='';source.setAttribute('aria-label','Источник людей: '+position.name);source.add(new Option('Добавить в экипаж', ''));
    for(const person of info.standalone.filter(p=>p.position_id===position.id)){
     const option=new Option('Перенести '+person.count+' чел. · '+person.scope+(person.compatible?'':' · другой период или режим'),String(person.id));option.disabled=!person.compatible;source.add(option);
    }
    const transferable=info.standalone.filter(p=>p.position_id===position.id&&p.compatible&&p.count>=info.count);
    if(!(assignment.crew_people||[]).some(p=>p.id===position.id)&&transferable.length===1)source.value=String(transferable[0].id);
    sourceLabel.append(source);fields.append(sourceLabel,el('p','rg-node-meta',''));fields.lastElementChild.dataset.crewQuantity='';row.append(fields);host.append(row);
   }
   update();filter();q('[data-crew-save]').disabled=false;
  }catch(e){error(e.message);}finally{busy=false;}
 });
 function close(){if(busy)return;if(dirty&&!confirm('Отменить несохранённые изменения экипажа?'))return;dirty=false;dialog.close();opener?.focus({preventScroll:true});status('saved','Сохранено');}
 dialog.querySelectorAll('[data-crew-cancel]').forEach(button=>button.addEventListener('click',close));dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
 q('[data-crew-search]').addEventListener('input',filter);
 form.addEventListener('input',event=>{if(event.target.matches('[data-crew-search]'))return;dirty=true;update();status('dirty','Есть изменения экипажа');});
 form.addEventListener('change',event=>{if(event.target.matches('[data-crew-search]'))return;dirty=true;update();status('dirty','Есть изменения экипажа');});
 dialog.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.type==='search')event.preventDefault();});
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!info)return;error('');busy=true;q('[data-crew-save]').disabled=true;status('saving','Сохранение экипажа…');
  try{
   const crew=selected().map(row=>{
    const sourceId=Number(row.querySelector('[data-crew-source]').value),person=info.standalone.find(p=>p.id===sourceId);
    return {position_id:Number(row.dataset.position),count:Number(row.querySelector('[data-crew-count]').value),is_replacement:row.querySelector('[data-crew-replacement]').checked,...(person?{source_assignment_id:person.id,source_revision:person.revision}:{})};
   });
   const result=await responseJSON(await fetch(cfg.crew_url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json','X-CSRFToken':document.querySelector('#work-resource-form [name=csrfmiddlewaretoken]').value},body:JSON.stringify({assignment_id:info.assignment_id,revision:info.revision,catalogue_revision:info.catalogue_revision,crew})}));
   document.dispatchEvent(new CustomEvent('gpr-graph-anchor',{detail:{id:result.assignment.id}}));dirty=false;dialog.close();const removed=new Set(result.removed.map(String));api.data.assignments=api.data.assignments.filter(a=>!removed.has(String(a.id)));
   for(const updated of result.updated){const index=api.data.assignments.findIndex(a=>a.id===updated.id);if(index>=0)api.data.assignments[index]=updated;}
   api.upsert(result.assignment);window.gprSaveUI?.updateWorkflow(result.workflow);
   status('saved','Экипаж сохранён в плане и справочнике');document.querySelector('[data-graph-hint]').textContent='Экипаж применён к технике и сохранён для будущих добавлений. Перенесённые люди учтены один раз.';
   document.querySelector('[data-graph-crew="'+result.assignment.id+'"]')?.focus({preventScroll:true});
  }catch(e){error(e.message);status('error','Экипаж не сохранён');}finally{busy=false;q('[data-crew-save]').disabled=false;}
 });
 window.addEventListener('beforeunload',event=>{if(dirty||busy){event.preventDefault();event.returnValue='';}});
})();
