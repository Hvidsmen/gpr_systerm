(() => {
 'use strict';
 const root=document.querySelector('[data-work-editor]');if(!root)return;
 const form=root.querySelector('form'),kind=document.getElementById('id_kind'),unit=document.getElementById('id_unit'),profile=document.getElementById('id_load_profile');
 const isNew=form.hasAttribute('data-new-record');
 function mode(){
  const composite=kind.value==='COMPOSITE';
  root.querySelectorAll('[data-composite-only]').forEach(el=>el.hidden=!composite&&!el.querySelector('.errorlist'));
  root.querySelectorAll('[data-simple-only]').forEach(el=>el.hidden=composite&&!el.querySelector('.errorlist'));
  unit.required=!composite;profile.required=!composite;
  document.getElementById('unit-required').hidden=composite;document.getElementById('profile-required').hidden=composite;
  root.querySelector('[data-work-name-label]').replaceChildren(document.createTextNode(composite?'Название комплекса работ ':'Название работы '),Object.assign(document.createElement('span'),{className:'text-danger',textContent:'*'}));
  root.querySelector('[data-work-unit-label]').textContent=composite?'Единица комплекса':'Единица измерения';
  if(isNew){root.querySelector('[data-work-editor-title]').textContent=composite?'Новая комплексная работа':'Новая самостоятельная работа';root.querySelector('[data-work-submit]').textContent=composite?'Создать и перейти к составу':'Создать работу';root.querySelector('[data-work-next-step]').textContent=composite?'Следующий шаг — состав и нормативы комплекса.':'Работа будет доступна для планирования.';}
 }
 kind.addEventListener('change',mode);mode();
 const data=JSON.parse(document.getElementById('work-hierarchy').textContent),project=document.getElementById('id_project'),object=document.getElementById('id_construction_object'),section=document.getElementById('id_section'),complex=document.getElementById('id_complex_object'),status=document.getElementById('location-status');
 const locked=new Set([project,object].filter(select=>select.disabled));
 function options(select,rows,placeholder,enabled,selected=''){select.replaceChildren(new Option(placeholder,''));rows.forEach(row=>select.add(new Option(row.name,String(row.id))));select.value=selected;select.disabled=locked.has(select)||!enabled;select.dispatchEvent(new Event('work-options-updated'));}
 function preview(){const labels=[project,object,section,complex].filter(select=>select.value).map(select=>select.selectedOptions[0]?.textContent).filter(Boolean);root.querySelector('[data-work-location-path]').textContent=labels.join(' → ');}
 function updateComplex(){const previous=complex.value;options(complex,(data.complex_objects||[]).filter(row=>String(row.section_id)===section.value),'Без комплексного объекта',Boolean(section.value),previous);preview();}
 function updateSections(selected=''){const rows=data.sections.filter(row=>String(row.construction_object_id)===object.value);options(section,rows,object.value?(rows.length?'Выберите раздел':'На объекте пока нет разделов'):'Сначала выберите объект',Boolean(object.value),selected);updateComplex();status.textContent=object.value&&!rows.length?'Создайте раздел в карточке строительного объекта, затем вернитесь к созданию работы.':'';}
 function updateObjects(selected='',selectedSection=''){const rows=data.objects.filter(row=>String(row.project_id)===project.value);options(object,rows,project.value?(rows.length?'Выберите объект':'В проекте пока нет объектов'):'Сначала выберите проект',Boolean(project.value),selected);updateSections(selectedSection);if(project.value&&!rows.length)status.textContent='В выбранном проекте пока нет строительных объектов.';}
 const initialObject=object.value,initialSection=section.value,initialComplex=complex.value;updateObjects(initialObject,initialSection);complex.value=initialComplex;preview();
 project.addEventListener('change',()=>updateObjects());object.addEventListener('change',()=>updateSections());section.addEventListener('change',updateComplex);complex.addEventListener('change',preview);
 root.querySelectorAll('.errorlist').forEach(errors=>{for(let node=errors.parentElement;node&&node!==form;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;});
 root.querySelectorAll('[data-work-error-link]').forEach(link=>link.addEventListener('click',event=>{const field=document.getElementById(link.hash.slice(1));if(!field)return;event.preventDefault();for(let node=field.parentElement;node&&node!==form;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;field.scrollIntoView({block:'center'});const button=field.parentElement.querySelector('.search-select-button');(button||field).focus();}));
 form.addEventListener('invalid',event=>{for(let node=event.target.parentElement;node&&node!==form;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;},true);
})();
