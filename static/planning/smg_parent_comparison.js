(() => {
 'use strict';
 const root=document.getElementById('smg-parent-comparison');if(!root)return;
 const rows=[...root.querySelectorAll('[data-comparison-row]')],buttons=[...root.querySelectorAll('[data-comparison-toggle]')];
 const storage='smg-parent-comparison:'+root.dataset.comparisonStorage;
 const known=new Set(buttons.map(button=>button.dataset.comparisonToggle));let collapsed=new Set();
 try{const saved=JSON.parse(localStorage.getItem(storage)||'[]');if(Array.isArray(saved))collapsed=new Set(saved.filter(key=>known.has(key)));}catch{}
 function render(){
  for(const row of rows)row.hidden=row.dataset.comparisonAncestors.split(' ').some(key=>collapsed.has(key));
  for(const button of buttons){
   const expanded=!collapsed.has(button.dataset.comparisonToggle);
   button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'▾':'▸';
   button.setAttribute('aria-label',(expanded?'Свернуть ':'Развернуть ')+button.closest('tr').querySelector('[data-comparison-label]').textContent);
  }
 }
 function save(){render();try{localStorage.setItem(storage,JSON.stringify([...collapsed]));}catch{}}
 for(const button of buttons)button.addEventListener('click',()=>{const key=button.dataset.comparisonToggle;collapsed.has(key)?collapsed.delete(key):collapsed.add(key);save();});
 root.querySelectorAll('[data-comparison-expand]').forEach(button=>button.addEventListener('click',()=>{collapsed=button.dataset.comparisonExpand==='true'?new Set():new Set(known);save();}));
 render();
})();
