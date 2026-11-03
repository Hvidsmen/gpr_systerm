(() => {
  'use strict';
  const data = document.getElementById('access-group-options');
  if (!data) return;
  const config = JSON.parse(data.textContent);
  const form = document.querySelector('[data-access-form]') || document;
  const group = form.querySelector('[name=access_group]');
  const role = form.querySelector('[name=role]');
  const objects = form.querySelector('[name=objects], [name=assigned_objects]');
  const additionalObjects = form.querySelector('[name=production_additional_objects]');
  const selectedAdditional = () => Array.from(additionalObjects?.selectedOptions || []);
  const hint = form.querySelector('[data-access-scope]');
  if (!group || !role || !objects) return;
  const limitedRoles = ['PLANNER', 'FOREMAN', 'DISPATCHER', 'PERSONNEL_DISPATCHER', 'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER', 'FUEL_WAREHOUSE'];
  let individual = null;
  const label = select => select.selectedOptions[0]?.textContent.trim() || '';
  const selectedObjects = () => Array.from(objects.selectedOptions);
  function explain() {
    const code = config.roles[role.value];
    const limited = limitedRoles.includes(code);
    const catalog = ['PERSONNEL_CATALOG_MANAGER', 'EQUIPMENT_CATALOG_MANAGER'].includes(code);
    let description = 'Выберите группу или роль, чтобы увидеть область доступа.';
    if (catalog) description = code === 'PERSONNEL_CATALOG_MANAGER' ? 'Только справочники сотрудников и должностей своей компании. Планы и факт недоступны.' : 'Только справочник техники своей компании. Планы и факт недоступны.';
    else if (limited) description = 'Доступ только к назначенным объектам. Если список пуст, доступа к объектам нет.';
    else if (code) description = 'Эта роль имеет доступ ко всем объектам своей компании в разрешённых ей разделах. Выбранный список объектов не ограничивает её доступ.';
    const production = ['DISPATCHER','ALL_FACT_DISPATCHER'].includes(code);
    if (production) description = 'По обоим спискам можно смотреть и изменять производственный факт. Ежедневные задания создаются только по основным объектам; по дополнительным — без заданий.';
    if (code === 'FUEL_WAREHOUSE') description += ' Ввод ГСМ за день; ВЖГ доступны по связям с назначенными объектами. Работы, люди и техника недоступны.';
    if (hint) {
      hint.textContent = description;
      hint.classList.toggle('is-warning', limited && !selectedObjects().length && !(production && selectedAdditional().length));
    }
    const set = (selector, text) => { const node = form.querySelector(selector); if (node) node.textContent = text; };
    set('[data-summary-group]', group.value ? label(group) : 'Индивидуальные права');
    set('[data-summary-role]', role.value ? label(role) : 'Пока не выбрана');
    set('[data-summary-scope]', catalog ? 'Работа со справочником компании' : limited ? selectedObjects().length ? 'Назначено объектов: ' + selectedObjects().length : 'Объекты не назначены — доступ к ним закрыт' : code ? 'Все объекты компании · в пределах роли' : 'Укажите роль или выберите группу.');
    if (production) {
      set('[data-summary-scope]', 'С ежедневными заданиями: ' + selectedObjects().length + '. Можно просматривать и изменять факт.');
    }
    const writable = new Set(selectedObjects().map(option => option.value));
    const extra = selectedAdditional().filter(option => !writable.has(option.value));
    set('[data-summary-additional-objects]', production && extra.length ? 'Дополнительно без ежедневных заданий: ' + extra.map(option => option.textContent.trim()).join('; ') : '');
    const chips = form.querySelector('[data-summary-objects]');
    if (chips) {
      chips.replaceChildren();
      if (limited) {
        for (const option of selectedObjects().slice(0, 6)) {
          const chip = document.createElement('span'); chip.textContent = option.textContent.trim(); chips.append(chip);
        }
        if (selectedObjects().length > 6) { const more = document.createElement('span'); more.textContent = 'Ещё ' + (selectedObjects().length - 6); chips.append(more); }
      }
    }
  }
  function changed(initial = false) {
    const selected = config.groups.find(item => String(item.id) === group.value);
    if (selected) {
      if (!individual && !initial) individual = {role: role.value, objects: selectedObjects().map(option => option.value), additional: selectedAdditional().map(option => option.value)};
      role.value = String(selected.role);
      const ids = selected.objects.map(String);
      for (const option of objects.options) option.selected = ids.includes(option.value);
      if (additionalObjects) for (const option of additionalObjects.options) option.selected = (selected.production_additional_objects || []).map(String).includes(option.value);
    } else if (individual) {
      role.value = individual.role;
      for (const option of objects.options) option.selected = individual.objects.includes(option.value);
      if (additionalObjects) for (const option of additionalObjects.options) option.selected = individual.additional.includes(option.value);
      individual = null;
    }
    role.disabled = Boolean(selected); objects.disabled = Boolean(selected); if (additionalObjects) additionalObjects.disabled = Boolean(selected);
    const note = form.querySelector('[data-inheritance-note]'); if (note) note.hidden = !selected;
    for (const field of [role, objects, additionalObjects].filter(Boolean)) field.dispatchEvent(new Event('change', {bubbles: true}));
    explain();
  }
  group.addEventListener('change', () => changed());
  role.addEventListener('change', explain); objects.addEventListener('change', explain);
  additionalObjects?.addEventListener('change', explain);
  changed(true);
})();
