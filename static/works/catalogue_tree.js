(() => {
  const tree = document.querySelector('.catalogue-tree');
  if (!tree) return;
  const details = Array.from(tree.querySelectorAll('details'));
  const level = () => details.forEach(node => {
    node.open = node.classList.contains('ct-object') || node.classList.contains('ct-section');
  });
  tree.querySelector('[data-tree-level]').addEventListener('click', level);
  tree.querySelector('[data-tree-expand]').addEventListener('click', () => details.forEach(node => { node.open = true; }));
  tree.querySelector('[data-tree-collapse]').addEventListener('click', () => details.forEach(node => { node.open = false; }));
  tree.querySelectorAll('summary input, summary a').forEach(control => control.addEventListener('click', event => event.stopPropagation()));
  const branches = Array.from(tree.querySelectorAll('[data-tree-object]'));
  const ids = Array.from(new Set(branches.map(node => node.dataset.treeObject)));
  const limit = tree.querySelector('[data-tree-limit]');
  const previous = tree.querySelector('[data-tree-prev]');
  const next = tree.querySelector('[data-tree-next]');
  let page = 1;
  try {
    const saved = localStorage.getItem('work-tree-page-size');
    if (['25', '50', '100', 'all'].includes(saved)) limit.value = saved;
  } catch (_) { /* Browser storage may be unavailable. */ }
  const render = () => {
    const size = limit.value === 'all' ? Math.max(ids.length, 1) : Number(limit.value);
    const pages = Math.max(1, Math.ceil(ids.length / size));
    page = Math.max(1, Math.min(page, pages));
    const shown = new Set(ids.slice((page - 1) * size, page * size));
    branches.forEach(node => { node.hidden = !shown.has(node.dataset.treeObject); });
    tree.querySelectorAll('.ct-section').forEach(section => {
      section.hidden = !section.querySelector('.ct-branch:not([hidden]), .ct-packages-direct');
    });
    tree.querySelectorAll('.ct-object').forEach(object => {
      object.hidden = !object.querySelector('.ct-section:not([hidden])');
    });
    tree.querySelector('[data-tree-count]').textContent = `Комплексных объектов: ${shown.size} из ${ids.length}`;
    tree.querySelector('[data-tree-page]').textContent = `${page} / ${pages}`;
    previous.disabled = page === 1;
    next.disabled = page === pages;
  };
  limit.addEventListener('change', () => {
    page = 1;
    try { localStorage.setItem('work-tree-page-size', limit.value); } catch (_) {}
    render();
  });
  previous.addEventListener('click', () => { page--; render(); });
  next.addEventListener('click', () => { page++; render(); });
  level();
  render();
})();
