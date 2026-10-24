(() => {
  const dialog = document.getElementById('planning-branch-picker');
  if (!dialog) return;
  const options = JSON.parse(document.getElementById('planning-branch-options').textContent);
  const select = dialog.querySelector('select[name="selection"]');
  const search = dialog.querySelector('[data-branch-search]');
  let currentLevel = 'object';
  const copy = {
    object: ['Добавить комплексный объект', 'Комплексный объект', 'Комплексный объект добавляется со всеми своими комплексами и работами.'],
    package: ['Добавить комплексную работу', 'Комплексная работа', 'Комплексная работа добавляется со всеми своими работами в составе своего комплексного объекта. Соседние комплексы не добавляются.'],
    work: ['Добавить работу', 'Работа', 'Работа добавляется в своём комплексе и комплексном объекте, без соседних работ. Можно выбрать и самостоятельную работу.']
  };
  document.querySelectorAll('[data-open-branch]').forEach(button => button.addEventListener('click', () => {
    const level = button.dataset.openBranch;
    const rows = options.filter(option => option.level === level);
    dialog.querySelector('#branch-picker-title').textContent = copy[level][0];
    dialog.querySelector('[data-branch-label]').textContent = copy[level][1];
    dialog.querySelector('[data-branch-description]').textContent = copy[level][2];
    currentLevel = level;
    search.value = '';
    select.replaceChildren(new Option(`Выберите: ${copy[level][1].toLowerCase()}`, ''));
    rows.forEach(row => select.add(new Option(row.label, row.value)));
    select.value = '';
    select.required = true;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    dialog.querySelector('[data-submit-branch]').disabled = rows.length === 0;
    dialog.querySelector('[data-branch-empty]').hidden = rows.length !== 0;
    dialog.showModal();
    search.focus();
  }));
  search.addEventListener('input', () => {
    const selected = select.value;
    const terms = search.value.trim().toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);
    const rows = options.filter(option => option.level === currentLevel && terms.every(term => option.label.toLocaleLowerCase('ru').includes(term)));
    select.replaceChildren(new Option(rows.length ? 'Выберите позицию' : 'По запросу ничего не найдено', ''));
    rows.forEach(row => select.add(new Option(row.label, row.value)));
    if (rows.some(row => row.value === selected)) select.value = selected;
    dialog.querySelector('[data-submit-branch]').disabled = rows.length === 0;
  });
  dialog.querySelectorAll('[data-close-branch]').forEach(button => button.addEventListener('click', () => dialog.close()));
})();
