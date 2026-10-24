(() => {
  const form = document.getElementById('bulk-delete-form');
  if (!form) return;
  const rows = Array.from(document.querySelectorAll('[data-bulk-row]'));
  const groups = Array.from(document.querySelectorAll('[data-bulk-complex]'));
  const sync = () => {
    const groupCount = groups.filter(group => group.checked).length;
    groups.forEach(group => {
      group.indeterminate = !group.checked && Array.from(group.closest('.ct-branch').querySelectorAll('[data-bulk-row]')).some(row => row.checked);
    });
    const count = rows.filter(row => row.checked).length;
    form.querySelector('[data-bulk-count]').textContent = groupCount ? `Выбрано: ${groupCount} комплексных объектов, ${count} работ на странице` : `Выбрано: ${count}`;
    const submit=form.querySelector('[data-bulk-submit]');
    if(submit)submit.disabled = count === 0 && groupCount === 0;
    const repair = form.querySelector('[data-bulk-structure]');
    if (repair) repair.disabled = groupCount > 0 || count === 0 || rows.some(row => row.checked && row.dataset.workKind !== 'SIMPLE');
    const append = form.querySelector('[data-bulk-append]');
    if (append) {
      const chosen = rows.filter(row => row.checked);
      append.disabled = groupCount > 0 || count === 0 || chosen.some(row => row.dataset.workKind !== 'SIMPLE') || new Set(chosen.map(row => row.dataset.workObject)).size > 1;
    }
    const merge = form.querySelector('[data-bulk-merge]');
    if (merge) {
      const chosen = rows.filter(row => row.checked);
      merge.disabled = groupCount > 0 || count < 2 || chosen.some(row => row.dataset.workKind !== 'SIMPLE') || new Set(chosen.map(row => row.dataset.workObject)).size > 1;
    }
    form.querySelectorAll('[data-bulk-equipment-merge], [data-bulk-catalog-merge]').forEach(button => {
      button.disabled = count < 2;
    });
    rows.forEach(row => {
      const record = row.closest('tr,li');
      record?.classList.toggle('table-active', row.checked);
      record?.classList.toggle('bg-primary-subtle', row.checked);
    });
  };
  groups.forEach(group => group.addEventListener('change', () => {
    group.closest('.ct-branch').querySelectorAll('[data-bulk-row]').forEach(row => {row.checked = group.checked;});
    sync();
  }));
  rows.forEach(row => row.addEventListener('change', () => {
    if (!row.checked) {
      const group = row.closest('.ct-branch')?.querySelector('[data-bulk-complex]');
      if (group) group.checked = false;
    }
    sync();
  }));
  form.querySelector('[data-bulk-select]').addEventListener('click', () => { rows.forEach(row => { row.checked = true; }); sync(); });
  form.querySelector('[data-bulk-clear]').addEventListener('click', () => { rows.forEach(row => { row.checked = false; }); groups.forEach(group => {group.checked = false;}); sync(); });
  sync();
})();
