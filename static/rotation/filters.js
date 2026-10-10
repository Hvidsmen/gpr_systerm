(() => {
  const root = document.getElementById('rotation-groups');
  if (!root) return;
  const search = document.getElementById('rotation-search');
  const select = document.getElementById('rotation-group');
  const groups = Array.from(root.querySelectorAll('.rotation-group'));
  const roles = Array.from(root.querySelectorAll('.rotation-role'));
  const apply = () => {
    const query = search.value.trim().toLocaleLowerCase('ru');
    for (const group of groups) {
      const matchesGroup = !select.value || group.dataset.group === select.value;
      for (const role of group.querySelectorAll('.rotation-role')) {
        role.hidden = !matchesGroup || !role.dataset.search.toLocaleLowerCase('ru').includes(query);
      }
      group.hidden = !Array.from(group.querySelectorAll('.rotation-role')).some(role => !role.hidden);
      if (!group.hidden && (query || select.value)) group.open = true;
    }
    document.getElementById('rotation-filter-count').textContent = `Показано должностей: ${roles.filter(role => !role.hidden).length} из ${roles.length}`;
  };
  search.addEventListener('input', apply);
  select.addEventListener('change', apply);
  document.getElementById('rotation-filter-reset').addEventListener('click', () => {search.value = ''; select.value = ''; apply();});
  document.querySelectorAll('[data-rotation-expand]').forEach(button => button.addEventListener('click', () => {
    root.querySelectorAll('.rotation-group, .rotation-role').forEach(node => {if (!node.hidden) node.open = button.dataset.rotationExpand === 'true';});
  }));
  apply();
})();
