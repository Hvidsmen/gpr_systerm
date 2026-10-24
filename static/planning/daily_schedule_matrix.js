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
  inputs.forEach(input => input.addEventListener('input',() => {
    input.classList.toggle('border-primary',input.value !== input.dataset.original);
    status.textContent = `Изменено ячеек: ${changed().length}. Сохраните изменения.`;
  }));
  window.addEventListener('beforeunload',event => {
    if (changed().length) { event.preventDefault(); event.returnValue = ''; }
  });
  if (add) {
    const selector = add.querySelector('[name="kind"]');
    const fields = {work:['selection'],labor:['brigade','rate'],equipment:['equipment','reference','rate'],fuel:['fuel','reference','rate']};
    const showFields = () => add.querySelectorAll('[data-add-field]').forEach(wrapper => {
      wrapper.hidden = wrapper.dataset.addField !== 'kind' && !fields[selector.value].includes(wrapper.dataset.addField);
    });
    selector.addEventListener('change',showFields); showFields();
    add.addEventListener('submit',event => {
      if (changed().length) { event.preventDefault(); status.textContent = 'Сначала сохраните дневные изменения, затем добавьте строку.'; save.scrollIntoView({block:'center'}); }
    });
  }
  if (save) save.addEventListener('click',async () => {
    const edits = changed();
    if (!edits.length) { status.textContent = 'Нет изменений для сохранения.'; return; }
    if (edits.some(input => !input.value || !input.checkValidity())) { status.textContent = 'Проверьте числа: значения должны быть неотрицательными и соответствовать шагу поля.'; return; }
    const entries = new Map();
    edits.forEach(input => {
      const body = input.closest('[data-smg-entry]');
      if (!entries.has(body.dataset.entryId)) entries.set(body.dataset.entryId,{id:body.dataset.entryId,revision:body.dataset.revision,changes:[]});
      entries.get(body.dataset.entryId).changes.push({date:input.dataset.date,item_id:input.dataset.item ? Number(input.dataset.item) : null,field:input.dataset.field,value:input.value});
    });
    save.disabled = true; status.textContent = 'Сохранение…';
    try {
      const response = await fetch(window.location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':add.querySelector('[name="csrfmiddlewaretoken"]').value},body:JSON.stringify({entries:[...entries.values()]})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось сохранить СМГ.');
      inputs.forEach(input => { input.dataset.original = input.value; });
      window.location.reload();
    } catch (error) { status.textContent = error.message || 'Ошибка сохранения. Изменения остаются в таблице.'; save.disabled = false; }
  });
  filter();
})();
