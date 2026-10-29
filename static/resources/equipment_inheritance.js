(()=>{
 const source=document.getElementById('equipment-parent-groups');if(!source)return;
 const groups=new Map(JSON.parse(source.textContent).map(row=>[String(row.id),String(row.category_id||'')]));
 const parent=document.getElementById('id_parent')||document.getElementById('id_type');
 const category=document.getElementById('id_category')||document.getElementById('id_catalogue_group');if(!parent||!category)return;
 const machine=category.name==='catalogue_group';
 function update(){const inherited=machine||Boolean(parent.value),option=parent.selectedOptions[0];category.disabled=inherited;
  if(inherited){category.value=option?.dataset.category??groups.get(parent.value)??'';category.dispatchEvent(new Event('change',{bubbles:true}));}
  document.querySelectorAll('[data-equipment-inline="group"][data-target="'+category.id+'"]').forEach(button=>button.hidden=inherited);
 }
 parent.addEventListener('change',update);update();
})();
