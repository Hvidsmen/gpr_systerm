(() => {
  const matrix = document.getElementById('plan-fact-matrix');
  if (!matrix) return;
  const setExpanded = (button, expanded) => {
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? '▾' : '▸';
    matrix.querySelectorAll('[data-overview-parent]').forEach(row => {
      if (row.dataset.overviewParent === button.dataset.overviewToggle) row.hidden = !expanded;
    });
  };
  matrix.querySelectorAll('[data-overview-toggle]').forEach(button => button.addEventListener('click', () => {
    setExpanded(button, button.getAttribute('aria-expanded') !== 'true');
  }));
  document.querySelectorAll('[data-expand-matrix]').forEach(button => button.addEventListener('click', () => {
    matrix.querySelectorAll('[data-overview-toggle]').forEach(toggle => setExpanded(toggle, button.dataset.expandMatrix === 'true'));
  }));
})();
