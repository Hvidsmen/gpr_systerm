(() => {
 const button=document.querySelector('[data-bulk-group-open]'),dialog=document.querySelector('[data-bulk-group-dialog]');
 if(!button||!dialog)return;
 const selected=()=>[...document.querySelectorAll('[data-bulk-row]:checked')];
 const sync=()=>{button.disabled=!selected().length;};
 document.addEventListener('change',sync);document.addEventListener('click',sync);window.addEventListener('pageshow',sync);sync();
 button.addEventListener('click',()=>{
  const rows=selected();if(!rows.length)return;
  dialog.querySelector('form').reset();
  const container=dialog.querySelector('[data-bulk-group-selection]');container.replaceChildren();
  rows.forEach(row=>{const input=document.createElement('input');input.type='hidden';input.name='selected';input.value=row.value;container.append(input);});
  dialog.querySelector('[data-bulk-group-count]').textContent='Выбрано должностей: '+rows.length+'.';
  dialog.showModal();
 });
 dialog.querySelectorAll('[data-bulk-group-close]').forEach(button=>button.addEventListener('click',()=>dialog.close()));
})();
