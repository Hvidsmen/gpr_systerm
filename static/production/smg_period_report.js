/* Lazy read-only report: save this editor first, never hide unsaved drafts. */
(()=>{
 const form=document.querySelector('[data-production-form]'),panel=form?.querySelector('[data-smg-period]');
 if(!panel)return;
 const footer=form.querySelector('.production-save-footer');
 if(footer)new ResizeObserver(()=>document.documentElement.style.setProperty('--production-footer-height',footer.getBoundingClientRect().height+'px')).observe(footer);
 const button=panel.querySelector('[data-period-refresh]'),state=panel.querySelector('[data-period-state]'),content=panel.querySelector('[data-period-content]');
 let busy=false,showDays=false;
 async function load(){
  if(busy)return;busy=true;button.disabled=true;state.textContent='Сохранение изменений и построение отчёта…';content.setAttribute('aria-busy','true');
  try{
   if(form.dataset.resetRequired==='1')throw new Error('Данные были сброшены администратором. Обновите страницу.');
   const save=window.productionAutoSave;
   if(save){const saved=await save.flush();if(saved===false||save.pending)throw new Error('Есть несохранённые изменения. Вернитесь к вводу, исправьте ошибки и повторите обновление отчёта.');}
   const query=new URLSearchParams({construction_object:form.elements.construction_object.value,date:form.elements.date.value});if(showDays)query.set('by_day','1');
   const response=await fetch(panel.dataset.url+'?'+query,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'},cache:'no-store'});
   if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('text/html'))throw new Error('Не удалось построить отчёт. Проверьте подключение и доступ к объекту.');
   content.innerHTML=await response.text();
   window.productionPeriodTabs?.init(content);window.productionReportFilters?.init(panel,content);panel.dispatchEvent(new Event('production-report-loaded'));
   state.textContent='Обновлено в '+new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())+'. Показаны сохранённые данные.';
  }catch(error){state.textContent=error.message+(content.children.length?' Ниже остаётся предыдущий результат.':'');}
  finally{busy=false;button.disabled=false;content.removeAttribute('aria-busy');}
 }
 button.addEventListener('click',load);
 panel.addEventListener('click',event=>{const toggle=event.target.closest('[data-period-days-toggle]');if(toggle&&!busy){showDays=toggle.getAttribute('aria-pressed')!=='true';load();}});
 form.addEventListener('production-step-change',event=>{if(event.detail===5)load();});
 // employee_graph initializes before this script.
 if(!panel.hidden)load();
})();
