(() => {
 'use strict';
 const root=document.querySelector('[data-equipment-catalogue]');if(!root)return;
 const categories=[...root.querySelectorAll('[data-equipment-category]')],types=[...root.querySelectorAll('[data-equipment-type]')];
 const key='equipment-folds:'+root.dataset.company;let saved={};
 try{saved=JSON.parse(localStorage.getItem(key)||'{}');}catch{}
 for(const node of [...categories,...types]){
  const category=node.closest('[data-equipment-category]');
  const id=node.hasAttribute('data-equipment-category')?'category:'+node.dataset.equipmentCategory:category?'category:'+category.dataset.equipmentCategory+':type:'+node.dataset.equipmentType:'planning:type:'+node.dataset.equipmentType;
  if(root.dataset.filtered!=='true'&&Object.hasOwn(saved,id))node.open=Boolean(saved[id]);
  node.addEventListener('toggle',()=>{saved[id]=node.open;try{localStorage.setItem(key,JSON.stringify(saved));}catch{}});
 }
 root.querySelectorAll('[data-equipment-fold]').forEach(button=>button.addEventListener('click',()=>{
  for(const node of [...categories,...types])node.open=button.dataset.equipmentFold==='open';
 }));
 root.querySelector('[data-equipment-select-page]')?.addEventListener('click',()=>root.querySelector('[data-bulk-select]')?.click());
 root.querySelectorAll('[data-equipment-select-category]').forEach(button=>button.addEventListener('click',()=>{
  const boxes=[...button.closest('[data-equipment-category]').querySelectorAll('[data-bulk-row]')];
  const selected=!boxes.every(box=>box.checked);
  for(const box of boxes){box.checked=selected;box.dispatchEvent(new Event('change',{bubbles:true}));}
 }));
 const menu=root.querySelector('.equipment-catalogue-menu');
 document.addEventListener('click',event=>{if(menu?.open&&!menu.contains(event.target))menu.open=false;});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu?.open){menu.open=false;menu.querySelector('summary').focus();}});
})();
