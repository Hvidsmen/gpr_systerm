(()=>{
 const host=document.querySelector('.position-catalog');if(!host)return;
 host.querySelectorAll('[data-position-expand],[data-position-collapse]').forEach(button=>button.addEventListener('click',()=>{host.querySelectorAll('[data-position-tree-group]').forEach(group=>group.open=button.hasAttribute('data-position-expand'));}));
 host.querySelectorAll('[data-position-select-all],[data-position-select-group]').forEach(box=>box.addEventListener('change',()=>{const wanted=box.checked,scope=box.hasAttribute('data-position-select-group')?box.closest('[data-position-tree-group]'):host;scope.querySelectorAll('[data-bulk-row]').forEach(row=>{row.checked=wanted;row.dispatchEvent(new Event('change',{bubbles:true}));});}));
 host.addEventListener('change',event=>{if(!event.target.matches('[data-bulk-row]'))return;host.querySelectorAll('[data-position-select-all],[data-position-select-group]').forEach(box=>{const scope=box.hasAttribute('data-position-select-group')?box.closest('[data-position-tree-group]'):host,rows=[...scope.querySelectorAll('[data-bulk-row]')],count=rows.filter(row=>row.checked).length;box.checked=count===rows.length;box.indeterminate=count>0&&count<rows.length;});});
 document.addEventListener('click',event=>host.querySelectorAll('.position-catalog-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
})();
