(function(){
  function funnel(){return document.querySelector('[data-recruitment-funnel]');}
  function error(message){
    const node=funnel()?.querySelector('[data-recruitment-funnel-error]');
    if(!node)return;
    node.textContent=message||'';node.classList.toggle('d-none',!message);
  }
  function setPositionOpen(row,open){
    const people=row.nextElementSibling;
    if(!people||!people.classList.contains('position-people-row'))return;
    row.setAttribute('aria-expanded',String(open));people.hidden=!open;
  }
  async function replaceFunnel(url,push){
    const current=funnel();if(!current)return;
    current.setAttribute('aria-busy','true');error('');
    try{
      const response=await fetch(url,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      if(!response.ok||response.redirected)throw new Error('Не удалось обновить воронку. Повторите попытку.');
      const page=new DOMParser().parseFromString(await response.text(),'text/html');
      const replacement=page.querySelector('[data-recruitment-funnel]');
      if(!replacement)throw new Error('Сервер вернул страницу без воронки.');
      current.replaceWith(replacement);
      if(push)history.pushState({recruitmentFunnel:true},'',url);
    }catch(reason){
      current.removeAttribute('aria-busy');error(reason.message||'Не удалось обновить воронку.');
    }
  }
  document.addEventListener('click',function(event){
    const detailsAction=event.target.closest('[data-position-details-action]');
    if(detailsAction){
      const open=detailsAction.dataset.positionDetailsAction==='expand';
      funnel()?.querySelectorAll('[data-position-toggle]').forEach(function(row){setPositionOpen(row,open);});
      return;
    }
    const positionRow=event.target.closest('[data-position-toggle]');
    if(positionRow){setPositionOpen(positionRow,positionRow.getAttribute('aria-expanded')!=='true');return;}
    const link=event.target.closest('[data-recruitment-stage-link]');
    if(!link||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();replaceFunnel(link.href,true);
  });
  document.addEventListener('keydown',function(event){
    const row=event.target.closest('[data-position-toggle]');
    if(!row||(event.key!=='Enter'&&event.key!==' '))return;
    event.preventDefault();setPositionOpen(row,row.getAttribute('aria-expanded')!=='true');
  });
  document.addEventListener('submit',async function(event){
    const form=event.target.closest('[data-candidate-stage-form]');if(!form)return;
    event.preventDefault();
    const button=form.querySelector('button[type="submit"]');const status=form.querySelector('[data-candidate-stage-status]');
    button.disabled=true;status.textContent='Сохраняем…';error('');
    try{
      const response=await fetch(form.action,{method:'POST',body:new FormData(form),credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      const payload=await response.json().catch(function(){return {};});
      if(!response.ok)throw new Error(payload.error||'Не удалось изменить этап.');
      status.textContent='Сохранено';
      await replaceFunnel(location.href,false);
    }catch(reason){
      button.disabled=false;status.textContent='';error(reason.message||'Не удалось изменить этап.');
    }
  });
  window.addEventListener('popstate',function(){if(funnel())replaceFunnel(location.href,false);});
})();
