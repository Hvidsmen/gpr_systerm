(()=>{
 function expandBranch(button,expanded){
  const table=button.closest('table');if(!table)return;
  button.setAttribute('aria-expanded',String(expanded));
  table.querySelectorAll('[data-report-branch]').forEach(branch=>{if(branch.dataset.reportBranch===button.dataset.reportToggle)branch.hidden=!expanded;});
 }
 document.addEventListener('click',event=>{
  const bulk=event.target.closest('[data-report-expand], [data-report-collapse]');
  if(bulk){
   const scope=bulk.closest('[data-report-scope]');if(!scope)return;
   const expanded=bulk.hasAttribute('data-report-expand');
   scope.querySelectorAll('details.production-report-section, details.production-report-days').forEach(section=>{section.open=expanded;});
   scope.querySelectorAll('[data-report-toggle]').forEach(button=>expandBranch(button,expanded));
   return;
  }
  const button=event.target.closest('[data-report-toggle]');if(!button)return;
  expandBranch(button,button.getAttribute('aria-expanded')!=='true');
 });
 const normalize=text=>String(text||'').trim().replace(/\s+/g,' ').toLocaleLowerCase('ru');
 function workTree(rows,catalogue){
  const sections=new Map();
  for(const row of rows){
   const meta=catalogue.get(row.token)||{},sectionKey=meta.section_id||0;
   if(!sections.has(sectionKey))sections.set(sectionKey,{key:sectionKey,label:meta.section||'Без раздела',works:new Map()});
   const section=sections.get(sectionKey),label=meta.title||meta.label||row.token,unit=meta.unit||'',key=JSON.stringify([normalize(label),unit,meta.kind||'SIMPLE']);
   if(!section.works.has(key))section.works.set(key,{key,label,unit,objects:new Map(),entries:[]});
   const work=section.works.get(key),complexKey=meta.complex_id||0;
   if(!work.objects.has(complexKey))work.objects.set(complexKey,{label:meta.complex||'Без комплексного объекта',entries:[]});
   work.objects.get(complexKey).entries.push(row);work.entries.push(row);
  }
  const compare=(a,b)=>a.label.localeCompare(b.label,'ru');
  for(const section of sections.values()){
   section.works=[...section.works.values()].sort(compare);
   for(const work of section.works)work.objects=[...work.objects.values()].sort(compare);
  }
  return [...sections.values()].sort(compare);
 }
 function quantity(entries){
  const values=entries.map(row=>Number(String(row.quantity).replace(',','.')));
  return values.every(Number.isFinite)?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(values.reduce((sum,n)=>sum+n,0)):'—';
 }
 window.productionReportGroups={workTree,quantity};
})();
