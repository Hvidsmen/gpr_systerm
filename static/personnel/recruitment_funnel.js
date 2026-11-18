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
  function inlineButton(candidateId){
    return Array.from(funnel()?.querySelectorAll('[data-candidate-inline-toggle]')||[]).find(function(button){
      return button.dataset.candidateId===String(candidateId);
    });
  }
  function closeInlineRow(row){
    if(!row)return;
    row.hidden=true;
    const button=inlineButton(row.dataset.candidateInlineRow);
    if(button)button.setAttribute('aria-expanded','false');
  }
  function closeOtherInlineRows(except){
    funnel()?.querySelectorAll('[data-candidate-inline-row]').forEach(function(row){if(row!==except)closeInlineRow(row);});
  }
  function initializeInlineForm(root){
    if(!root||root.dataset.inlineInitialized==='true')return;
    root.dataset.inlineInitialized='true';
    [
      ['ready_for_arrival','ready_for_arrival_on'],
      ['manager_approved','manager_approved_on'],
      ['security_approved','security_approved_on'],
      ['medical_exam_passed','medical_exam_on']
    ].forEach(function(names){
      const flag=root.querySelector('[name="'+names[0]+'"]');
      const date=root.querySelector('[name="'+names[1]+'"]');
      if(!flag||!date)return;
      const sync=function(){date.required=flag.checked;};
      flag.addEventListener('change',sync);sync();
    });
    const city=root.querySelector('[name="base_city"]');
    const newCity=root.querySelector('[name="new_base_city"]');
    if(city&&newCity){
      city.addEventListener('change',function(){if(city.value)newCity.value='';});
      newCity.addEventListener('input',function(){if(newCity.value.trim())city.value='';});
    }
  }
  async function loadInlineCandidate(button,forceOpen){
    const personRow=button.closest('tr');
    const row=personRow?.nextElementSibling;
    if(!row||!row.matches('[data-candidate-inline-row]'))return;
    if(!forceOpen&&!row.hidden){closeInlineRow(row);return;}
    closeOtherInlineRows(row);row.hidden=false;button.setAttribute('aria-expanded','true');
    const target=row.querySelector('[data-candidate-inline-target]');
    if(!target||target.dataset.loaded==='true'){initializeInlineForm(target);return;}
    target.textContent='Загрузка карточки…';
    try{
      const response=await fetch(button.dataset.candidateInlineUrl,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      if(!response.ok||response.redirected)throw new Error('Не удалось загрузить карточку.');
      target.innerHTML=await response.text();target.dataset.loaded='true';initializeInlineForm(target);
    }catch(reason){target.innerHTML='<div class="alert alert-danger mb-0" role="alert"></div>';target.firstElementChild.textContent=reason.message||'Не удалось загрузить карточку.';}
  }
  async function replaceFunnel(url,push){
    const current=funnel();if(!current)return false;
    current.setAttribute('aria-busy','true');error('');
    try{
      const response=await fetch(url,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      if(!response.ok||response.redirected)throw new Error('Не удалось обновить воронку. Повторите попытку.');
      const page=new DOMParser().parseFromString(await response.text(),'text/html');
      const replacement=page.querySelector('[data-recruitment-funnel]');
      if(!replacement)throw new Error('Сервер вернул страницу без воронки.');
      current.replaceWith(replacement);
      if(push)history.pushState({recruitmentFunnel:true},'',url);
      return true;
    }catch(reason){
      current.removeAttribute('aria-busy');error(reason.message||'Не удалось обновить воронку.');return false;
    }
  }
  document.addEventListener('click',function(event){
    const inlineToggle=event.target.closest('[data-candidate-inline-toggle]');
    if(inlineToggle){event.preventDefault();loadInlineCandidate(inlineToggle,false);return;}
    const inlineClose=event.target.closest('[data-candidate-inline-close]');
    if(inlineClose){event.preventDefault();closeInlineRow(inlineClose.closest('[data-candidate-inline-row]'));return;}
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
    const inlineForm=event.target.closest('[data-candidate-inline-form]');
    if(inlineForm){
      event.preventDefault();
      const button=inlineForm.querySelector('button[type="submit"]');
      const status=inlineForm.querySelector('[data-candidate-inline-status]');
      const target=inlineForm.closest('[data-candidate-inline-target]');
      if(button)button.disabled=true;if(status)status.textContent='Сохраняем…';error('');
      try{
        const response=await fetch(inlineForm.action,{method:'POST',body:new FormData(inlineForm),credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
        const html=await response.text();
        if(response.redirected||(!response.ok&&response.status!==422))throw new Error('Не удалось сохранить карточку.');
        if(response.status===422){
          target.innerHTML=html;target.dataset.loaded='true';delete target.dataset.inlineInitialized;initializeInlineForm(target);return;
        }
        const candidateId=inlineForm.dataset.candidateId;
        if(!await replaceFunnel(location.href,false))throw new Error('Карточка сохранена, но список не удалось обновить. Обновите страницу.');
        const refreshedButton=inlineButton(candidateId);
        if(refreshedButton){
          await loadInlineCandidate(refreshedButton,true);
          const refreshedCard=refreshedButton.closest('tr')?.nextElementSibling?.querySelector('[data-candidate-inline-card]');
          if(refreshedCard){const notice=document.createElement('div');notice.className='alert alert-success py-2';notice.setAttribute('role','status');notice.textContent='Карточка сохранена.';refreshedCard.prepend(notice);}
        }
      }catch(reason){
        if(button)button.disabled=false;if(status)status.textContent='';error(reason.message||'Не удалось сохранить карточку.');
      }
      return;
    }
    const form=event.target.closest('[data-candidate-stage-form]');if(!form)return;
    event.preventDefault();
    const button=form.querySelector('button[type="submit"]');const status=form.querySelector('[data-candidate-stage-status]');
    button.disabled=true;status.textContent='Сохраняем…';error('');
    try{
      const response=await fetch(form.action,{method:'POST',body:new FormData(form),credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});
      const payload=await response.json().catch(function(){return {};});
      if(!response.ok)throw new Error(payload.error||'Не удалось изменить этап.');
      status.textContent='Сохранено';await replaceFunnel(location.href,false);
    }catch(reason){button.disabled=false;status.textContent='';error(reason.message||'Не удалось изменить этап.');}
  });
  window.addEventListener('popstate',function(){if(funnel())replaceFunnel(location.href,false);});
})();
