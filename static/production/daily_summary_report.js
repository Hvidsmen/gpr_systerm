/* Daily plan/fact uses the same saved report and renderer as the monthly SMG tab. */
(()=>{
 const form=document.querySelector('[data-production-form]'),panel=form?.querySelector('[data-daily-summary]');if(!panel)return;
 const button=panel.querySelector('[data-daily-summary-refresh]'),state=panel.querySelector('[data-daily-summary-state]'),content=panel.querySelector('[data-daily-summary-content]');let busy=false;
 async function load(){
  if(busy)return;busy=true;button.disabled=true;state.textContent='Сохранение изменений и построение дневного отчёта…';content.setAttribute('aria-busy','true');
  try{
   if(form.dataset.resetRequired==='1')throw new Error('Данные были сброшены администратором. Обновите страницу.');
   const save=window.productionAutoSave;if(save){const saved=await save.flush();if(saved===false||save.pending)throw new Error('Есть несохранённые изменения. Исправьте ошибки и повторите обновление отчёта.');}
   if(form.productionLinks&&!await form.productionLinks.flush())throw new Error('Дождитесь отправки связей сотрудников с работами.');
   const query=new URLSearchParams({construction_object:form.elements.construction_object.value,date:form.elements.date.value,scope:'day'});
   const response=await fetch(panel.dataset.url+'?'+query,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'},cache:'no-store'});
   if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('text/html'))throw new Error('Не удалось построить дневной отчёт. Проверьте подключение и доступ к проекту.');
   content.innerHTML=await response.text();window.productionPeriodTabs?.init(content);
   state.textContent='Обновлено в '+new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())+'. Показаны сохранённые данные за день.';
  }catch(error){state.textContent=error.message+(content.children.length?' Ниже остаётся предыдущий результат.':'');}
  finally{busy=false;button.disabled=false;content.removeAttribute('aria-busy');}
 }
 button.addEventListener('click',load);form.addEventListener('production-step-change',event=>{if(event.detail===4)load();});if(!panel.hidden)load();
})();
