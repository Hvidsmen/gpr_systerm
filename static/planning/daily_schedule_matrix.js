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
  filter();
})();
