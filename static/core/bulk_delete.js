(() => {
  const form = document.getElementById('bulk-delete-form');
  if (!form) return;
  const rows = Array.from(document.querySelectorAll('[data-bulk-row]'));
  const sync = () => {
    const count = rows.filter(row => row.checked).length;
    form.querySelector('[data-bulk-count]').textContent = `Выбрано: ${count}`;
    form.querySelector('[data-bulk-submit]').disabled = count === 0;
    const merge = form.querySelector('[data-bulk-merge]');
    if (merge) {
      const chosen = rows.filter(row => row.checked);
      merge.disabled = count < 2 || chosen.some(row => row.dataset.workKind !== 'SIMPLE') || new Set(chosen.map(row => row.dataset.workObject)).size > 1;
    }
    rows.forEach(row => {
      const record = row.closest('tr,li');
      record?.classList.toggle('table-active', row.checked);
      record?.classList.toggle('bg-primary-subtle', row.checked);
    });
  };
  rows.forEach(row => row.addEventListener('change', sync));
  form.querySelector('[data-bulk-select]').addEventListener('click', () => { rows.forEach(row => { row.checked = true; }); sync(); });
  form.querySelector('[data-bulk-clear]').addEventListener('click', () => { rows.forEach(row => { row.checked = false; }); sync(); });
  sync();
})();
