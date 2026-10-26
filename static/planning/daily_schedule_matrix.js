(() => {
  const root = document.getElementById('smg-matrix');
  if (!root) return;
  const search = document.getElementById('smg-search');
  const kind = document.getElementById('smg-kind');
  const deviations = document.getElementById('smg-deviations');
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    root.querySelectorAll('[data-smg-section]').forEach(section => {
      let matches = 0;
      section.querySelectorAll('[data-smg-entry]').forEach(entry => {
        entry.hidden = !entry.dataset.name.toLocaleLowerCase().includes(query)
          || (deviations.checked && entry.dataset.deviation !== 'true');
        if (!entry.hidden) matches++;
      });
      section.querySelectorAll('[data-smg-group]').forEach(group => {
        group.hidden = ![...group.querySelectorAll('[data-smg-entry]')].some(entry => !entry.hidden);
      });
      section.hidden = (kind.value && kind.value !== section.dataset.smgSection) || !matches;
      if (!section.hidden) count += matches;
    });
    document.getElementById('smg-empty').hidden = count > 0;
  }
  [search,kind,deviations].forEach(field => field.addEventListener('input',filter));
  document.getElementById('smg-reset').addEventListener('click',() => {
    search.value = ''; kind.value = ''; deviations.checked = false; filter();
  });
  document.querySelectorAll('[data-smg-expand]').forEach(button => button.addEventListener('click',() => {
    root.querySelectorAll('details').forEach(details => { details.open = button.dataset.smgExpand === 'true'; });
  }));
  const save = document.getElementById('smg-save-table');
  const status = document.getElementById('smg-save-status');
  const add = document.getElementById('smg-add-entry');
  const inputs = [...root.querySelectorAll('.smg-day-input')];
  const changed = () => inputs.filter(input => input.value !== input.dataset.original);
  let saving = false;
  function message(text,state) { status.textContent = text; status.dataset.state = state; }
  function refresh() {
    const edits = changed();
    inputs.forEach(input => input.classList.toggle('border-primary',input.value !== input.dataset.original));
    message(edits.length ? `Есть изменения · ${edits.length} ячеек` : 'Сохранено',edits.length ? 'dirty' : 'saved');
  }
  inputs.forEach(input => input.addEventListener('input',refresh));
  window.addEventListener('beforeunload',event => {
    if (changed().length || saving) { event.preventDefault(); event.returnValue = ''; }
  });
  function requireSaved() {
    if (!changed().length && !saving) return true;
    message(saving ? 'Сохранение…' : 'Есть изменения. Сначала сохраните СМГ.',saving ? 'saving' : 'dirty');
    save?.focus({preventScroll:true});return false;
  }
  document.querySelector('.smg-editor').addEventListener('submit',event => { if (!requireSaved()) event.preventDefault(); });
  document.querySelectorAll('[data-smg-action-header] a,#smg-more-actions a').forEach(link => link.addEventListener('click',event => {
    if (!link.getAttribute('href').startsWith('#') && !requireSaved()) event.preventDefault();
  }));
  const switcher=document.querySelector('[data-smg-month-switch]');
  switcher?.addEventListener('change',()=>{if(requireSaved())window.location.assign(switcher.value);else switcher.value=window.location.pathname;});
  const handoff=document.querySelector('[data-smg-handoff]');
  document.querySelector('[data-smg-open-handoff]')?.addEventListener('click',()=>{if(requireSaved())handoff.showModal();});
  document.querySelector('[data-smg-close-handoff]')?.addEventListener('click',()=>handoff.close());
  const more=document.getElementById('smg-more-actions'),moreButton=document.querySelector('[popovertarget="smg-more-actions"]');
  function positionMore(){if(!more?.matches(':popover-open'))return;const rect=moreButton.getBoundingClientRect();more.style.margin='0';more.style.inset='auto';more.style.top=Math.min(rect.bottom+5,window.innerHeight-more.offsetHeight-8)+'px';more.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-more.offsetWidth-8))+'px';}
  more?.addEventListener('toggle',positionMore);window.addEventListener('resize',positionMore);window.addEventListener('scroll',positionMore,{passive:true});
  if (add) {
    const selector = add.querySelector('[name="kind"]');
    const fields = {work:['selection'],labor:['brigade','rate'],equipment:['equipment','rate'],fuel:['fuel','rate']};
    const showFields = () => add.querySelectorAll('[data-add-field]').forEach(wrapper => {
      wrapper.hidden = wrapper.dataset.addField !== 'kind' && !(fields[selector.value] || []).includes(wrapper.dataset.addField);
    });
    selector.addEventListener('change',showFields); showFields();
  }
  if (save) save.addEventListener('click',async () => {
    const edits = changed();
    if (!edits.length) { message('Сохранено','saved'); return; }
    if (edits.some(input => !input.value || !input.checkValidity())) { message('Проверьте числа: значения должны быть неотрицательными и соответствовать шагу поля.','error'); return; }
    const entries = new Map();
    edits.forEach(input => {
      const body = input.closest('[data-smg-entry]');
      if (!entries.has(body.dataset.entryId)) entries.set(body.dataset.entryId,{id:body.dataset.entryId,revision:body.dataset.revision,changes:[]});
      entries.get(body.dataset.entryId).changes.push({date:input.dataset.date,item_id:input.dataset.item ? Number(input.dataset.item) : null,field:input.dataset.field,value:input.value});
    });
    save.disabled = true; saving=true;inputs.forEach(input=>input.disabled=true);message('Сохранение…','saving');
    try {
      const response = await fetch(window.location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('.smg-editor [name="csrfmiddlewaretoken"]').value},body:JSON.stringify({entries:[...entries.values()]})});
      const result = await response.json().catch(()=>{throw new Error('Сервер не подтвердил сохранение СМГ. Повторите попытку.');});
      if (!response.ok) throw new Error(result.error || 'Не удалось сохранить СМГ.');
      edits.forEach(input => { input.dataset.original = input.value; });
      saving=false;message('Сохранено','saved');window.location.reload();
    } catch (error) { saving=false;message((error.message || 'Ошибка сохранения.')+' Изменения остаются в таблице.','error');save.disabled=false;inputs.forEach(input=>input.disabled=false); }
  });
  filter();
})();
