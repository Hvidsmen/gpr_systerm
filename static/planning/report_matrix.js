(() => {
  const form = document.getElementById('matrix-filters');
  const matrix = document.getElementById('plan-fact-matrix');
  if (!form) return;
  form.querySelectorAll('[name="start"],[name="end"]').forEach(field => {
    field.addEventListener('change', () => { form.querySelector('[name="month"]').value = ''; });
  });
  form.querySelectorAll('[data-section-toggle]').forEach(toggle => {
    toggle.addEventListener('change', () => {
      form.requestSubmit();
    });
  });
  const consolidated = form.querySelector('[name="consolidated"]');
  const versions = form.querySelectorAll('[name^="version_"]');
  const syncVersions = () => {
    const selected = Boolean(consolidated && consolidated.value);
    versions.forEach(field => { field.disabled = selected; if (selected) field.value = ''; });
  };
  consolidated?.addEventListener('change', () => {
    syncVersions();
    if (!consolidated.value) {
      form.querySelectorAll('[name="start"],[name="end"],[name="month"]').forEach(field => { field.disabled = false; });
    }
    if (consolidated.value) {
      // The selected composition supplies its own period unless the user applies a narrower one later.
      form.querySelector('[name="start"]').disabled = true;
      form.querySelector('[name="end"]').disabled = true;
      form.querySelector('[name="month"]').disabled = true;
      form.querySelector('[name="project"]').value = '';
      const objects = form.querySelector('[name="objects"]');
      if (objects) Array.from(objects.options).forEach(option => { option.selected = false; });
      form.requestSubmit();
    }
  });
  const project = form.querySelector('[name="project"]');
  project?.addEventListener('change', () => {
    if (consolidated) consolidated.value = '';
    versions.forEach(field => { field.value = ''; field.disabled = false; });
    const objects = form.querySelector('[name="objects"]');
    if (objects) Array.from(objects.options).forEach(option => { option.selected = false; });
    form.requestSubmit();
  });
  syncVersions();
  document.querySelectorAll('[data-expand-matrix]').forEach(button => button.addEventListener('click', () => {
    matrix?.querySelectorAll('details').forEach(details => { details.open = button.dataset.expandMatrix === 'true'; });
  }));
  matrix?.querySelectorAll('[data-work-toggle]').forEach(button => button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? '▾' : '▸';
    matrix.querySelectorAll('[data-work-parent]').forEach(row => {
      if (row.dataset.workParent === button.dataset.workToggle) row.hidden = !expanded;
    });
  }));
})();
