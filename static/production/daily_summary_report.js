/* Daily plan/fact uses the same saved report and renderer as the monthly SMG tab. */
(()=>{
 const form=document.querySelector('[data-production-form]'),panel=form?.querySelector('[data-daily-summary]');if(!panel)return;
 const button=panel.querySelector('[data-daily-summary-refresh]'),state=panel.querySelector('[data-daily-summary-state]'),content=panel.querySelector('[data-daily-summary-content]'),summary=panel.querySelector('[data-daily-control-summary]');let busy=false;
 function field(row,name){return row.querySelector(`[name$="-${name}"]`);}
 let totals=null;
 function renderSummary(){
  const rows=[...form.querySelectorAll('[data-work-fact-row]')],fallback={with_fact:rows.filter(row=>field(row,'actual_quantity')?.value.trim()!=='').length,missing:rows.filter(row=>row.dataset.planned==='1'&&field(row,'actual_quantity')?.value.trim()==='').length,unplanned:rows.filter(row=>row.dataset.planned!=='1'&&field(row,'actual_quantity')?.value.trim()!=='').length,employees:new Set([...form.querySelectorAll('[data-production-person]')].filter(row=>field(row,'employee')?.value&&!field(row,'DELETE')?.checked).map(row=>field(row,'employee').value)).size},values=totals||fallback;
  summary.replaceChildren();for(const [label,value,kind] of [['Работ с фактом',values.with_fact,''],['Плановых без факта',values.missing,values.missing?'is-warning':''],['Вне СМГ',values.unplanned,''],['Сотрудников',values.employees,''],['Состояние',totals?'Все данные за день':'Текущая страница','']]){const card=document.createElement('div');card.className=kind;const strong=document.createElement('strong');strong.textContent=value;card.append(strong,label);summary.append(card);}
 }
 async function load(){
  if(busy)return;busy=true;button.disabled=true;state.textContent='Сохранение изменений и построение дневного отчёта…';content.setAttribute('aria-busy','true');
  form.productionStatus?.start('Загружаем сводный отчёт…');
  try{
   if(form.dataset.resetRequired==='1')throw new Error('Данные были сброшены администратором. Обновите страницу.');
   const save=window.productionAutoSave;if(save){const saved=await save.flush();if(saved===false||save.pending)throw new Error('Есть несохранённые изменения. Исправьте ошибки и повторите обновление отчёта.');}
   if(form.productionLinks&&!await form.productionLinks.flush())throw new Error('Дождитесь отправки связей сотрудников с работами.');
   const query=new URLSearchParams({construction_object:form.elements.construction_object.value,date:form.elements.date.value,scope:'day'});
   const [response,summaryResponse]=await Promise.all([fetch(panel.dataset.url+'?'+query,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'},cache:'no-store'}),fetch(panel.dataset.summaryUrl+'?'+query,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'},cache:'no-store'})]);
   if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('text/html'))throw new Error('Не удалось построить дневной отчёт. Проверьте подключение и доступ к проекту.');
   if(!summaryResponse.ok)throw new Error('Не удалось получить итоги за весь день. Повторите обновление отчёта.');totals=await summaryResponse.json();renderSummary();
   content.innerHTML=await response.text();window.productionPeriodTabs?.init(content);window.productionReportFilters?.init(panel,content);panel.dispatchEvent(new Event('production-report-loaded'));
   state.textContent='Обновлено в '+new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())+'. Показаны сохранённые данные за день.';form.productionStatus?.done('Сводный отчёт загружен');
  }catch(error){state.textContent=error.message+(content.children.length?' Ниже остаётся предыдущий результат.':'');form.productionStatus?.error(error.message);}
  finally{busy=false;button.disabled=false;content.removeAttribute('aria-busy');}
 }
 button.addEventListener('click',load);form.addEventListener('change',renderSummary);form.addEventListener('production-report-update',renderSummary);form.addEventListener('production-step-change',event=>{if(event.detail===5){renderSummary();load();}});renderSummary();if(!panel.hidden)load();
})();
