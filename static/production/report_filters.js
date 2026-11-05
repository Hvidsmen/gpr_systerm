(()=>{
 'use strict';
 function init(panel,content){
  if(!panel||panel.dataset.reportFiltersReady==='1')return;panel.dataset.reportFiltersReady='1';
  const search=panel.querySelector('[data-report-search]'),mode=panel.querySelector('[data-report-mode]');
  function hasValue(text){const value=text.trim();return value!==''&&value!=='—'&&!/^0(?:[,.]0+)?(?:\s|$)/.test(value);}
  function apply(){
   const query=(search?.value||'').trim().toLocaleLowerCase('ru'),selected=mode?.value||'all';
   content.querySelectorAll('table tbody tr').forEach(row=>{
    if(row.classList.contains('report-day-row'))return;
    const cells=[...row.children],text=row.textContent.toLocaleLowerCase('ru'),fact=cells[3]?.textContent||'',deviation=cells[4]?.textContent||'';
    const matchesMode=selected==='actual'?hasValue(fact):selected==='missing'?!hasValue(fact):selected==='deviation'?hasValue(deviation):true;
    row.hidden=Boolean((query&&!text.includes(query))||!matchesMode);
   });
   content.querySelectorAll('.production-report-section').forEach(section=>{const rows=[...section.querySelectorAll('table tbody tr')].filter(row=>!row.classList.contains('report-day-row'));section.hidden=rows.length>0&&rows.every(row=>row.hidden);});
  }
  search?.addEventListener('input',apply);mode?.addEventListener('change',apply);panel.addEventListener('production-report-loaded',apply);apply();
 }
 window.productionReportFilters={init};
})();
