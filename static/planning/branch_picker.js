(() => {
  const dialog = document.getElementById('planning-branch-picker');
  if (!dialog) return;
  const options = JSON.parse(document.getElementById('planning-branch-options').textContent);
  const select = dialog.querySelector('select[name="selection"]');
  const search = dialog.querySelector('[data-branch-search]');
  const objectFilter = dialog.querySelector('[data-branch-object]');
  const packageFilter = dialog.querySelector('[data-branch-package]');
  let currentLevel = 'object';
  const copy = {
    object: ['Добавить комплексный объект', 'Комплексный объект', 'Комплексный объект добавляется со всеми своими комплексами и работами.'],
    package: ['Добавить комплексную работу', 'Комплексная работа', 'Комплексная работа добавляется со всеми своими работами в составе своего комплексного объекта. Соседние комплексы не добавляются.'],
    work: ['Добавить работу', 'Работа', 'Работа добавляется в своём комплексе и комплексном объекте, без соседних работ. Можно выбрать и самостоятельную работу.']
  };
  const matchesObject = row => !objectFilter.value || (objectFilter.value === '__none__' ? !row.object_id : row.object_id === objectFilter.value);
  function rebuildPackages() {
    const previous = packageFilter.value;
    const rows = options.filter(row => row.level === 'package' && matchesObject(row));
    packageFilter.replaceChildren(new Option('Все комплексные работы', ''));
    rows.forEach(row => packageFilter.add(new Option(row.label, row.package_id)));
    if (rows.some(row => row.package_id === previous)) packageFilter.value = previous;
  }
  function refresh() {
    const previous = select.value;
    const terms = search.value.trim().toLocaleLowerCase('ru').split(/\s+/).filter(Boolean);
    const rows = options.filter(row => row.level === currentLevel &&
      (currentLevel === 'object' || matchesObject(row)) &&
      (currentLevel !== 'work' || !packageFilter.value || row.package_id === packageFilter.value) &&
      terms.every(term => row.label.toLocaleLowerCase('ru').includes(term)));
    select.replaceChildren(new Option(rows.length ? `Выберите: ${copy[currentLevel][1].toLowerCase()}` : 'По запросу ничего не найдено', ''));
    rows.forEach(row => select.add(new Option(row.label, row.value)));
    if (rows.some(row => row.value === previous)) select.value = previous;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    dialog.querySelector('[data-branch-empty]').hidden = rows.length !== 0;
  }
  select.addEventListener('change', () => {dialog.querySelector('[data-submit-branch]').disabled = !select.value;});
  objectFilter.addEventListener('change', () => {rebuildPackages(); refresh();});
  packageFilter.addEventListener('change', refresh);
  search.addEventListener('input', refresh);
  document.querySelectorAll('[data-open-branch]').forEach(button => button.addEventListener('click', () => {
    if(window.planWorkbench&&!window.planWorkbench.canLeave())return;
    currentLevel = button.dataset.openBranch;
    const levelField=dialog.querySelector('[name=branch_level]');if(levelField)levelField.value=currentLevel;
    dialog.querySelector('#branch-picker-title').textContent = copy[currentLevel][0];
    dialog.querySelector('[data-branch-label]').textContent = copy[currentLevel][1];
    dialog.querySelector('[data-branch-description]').textContent = copy[currentLevel][2];
    search.value = '';
    objectFilter.replaceChildren(new Option('Все комплексные объекты', ''));
    options.filter(row => row.level === 'object').forEach(row => objectFilter.add(new Option(row.label, row.object_id)));
    if (options.some(row => row.level !== 'object' && !row.object_id)) objectFilter.add(new Option('Без комплексного объекта', '__none__'));
    objectFilter.value = '';
    packageFilter.value = '';
    rebuildPackages();
    dialog.querySelector('[data-branch-object-filter]').hidden = currentLevel === 'object';
    dialog.querySelector('[data-branch-package-filter]').hidden = currentLevel !== 'work';
    select.value = '';
    select.required = true;
    refresh();
    dialog.showModal();
    search.focus();
  }));
  if(dialog.dataset.openLevel)requestAnimationFrame(()=>document.querySelector('[data-open-branch="'+dialog.dataset.openLevel+'"]')?.click());
  dialog.querySelectorAll('[data-close-branch]').forEach(button => button.addEventListener('click', () => dialog.close()));
})();
