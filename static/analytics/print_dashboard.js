(() => {
  const dashboard=document.querySelector('.revenue-dashboard');if(!dashboard)return;
  function clean(){document.getElementById('gpr-print-document')?.remove();}
  function prepare(){
    clean();
    const copy=dashboard.cloneNode(true);copy.id='gpr-print-document';
    copy.querySelectorAll('details').forEach(item=>item.open=true);
    copy.querySelectorAll('tr[hidden]').forEach(item=>item.removeAttribute('hidden'));
    const form=copy.querySelector('#revenue-filters');
    if(form){
      const summary=document.createElement('p');summary.className='print-filter-summary';
      const labels=[];
      dashboard.querySelectorAll('#revenue-filters select,#revenue-filters input').forEach(field=>{
        if(!field.name||field.type==='hidden'||field.type==='checkbox'&&!field.checked)return;
        const label=dashboard.querySelector(`label[for="${field.id}"]`)?.textContent.trim();
        const value=field.tagName==='SELECT'?[...field.selectedOptions].filter(option=>option.value).map(option=>option.textContent.trim()).join(', '):field.value;
        if(label&&value)labels.push(`${label}: ${value}`);
      });
      summary.textContent=labels.join(' · ');form.replaceWith(summary);
    }
    copy.querySelectorAll('button,.btn,.chart-series-legend').forEach(item=>{
      if(item.matches('[data-revenue-select],[data-resource-select],.resource-share-item')){
        const label=document.createElement('span');label.className=item.className;label.replaceChildren(...item.childNodes);item.replaceWith(label);
      }else if(!item.classList.contains('chart-series-legend'))item.remove();
    });
    copy.querySelectorAll('[id]').forEach(item=>item.removeAttribute('id'));
    document.body.append(copy);
  }
  window.addEventListener('beforeprint',prepare);
  window.addEventListener('afterprint',clean);
  document.querySelector('[data-dashboard-print]')?.addEventListener('click',()=>window.print());
})();
