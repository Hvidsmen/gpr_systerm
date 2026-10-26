(() => {
  const tree = document.querySelector('.catalogue-tree');
  if (!tree) return;
  const details = Array.from(tree.querySelectorAll('details[data-tree-key]'));
  const key = `work-catalogue-tree:${tree.dataset.treeUser}:${location.pathname}${location.search}`;
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(key)); } catch (_) {}
  const level = (packages = false) => details.forEach(node => {
    node.open = node.classList.contains('ct-object') || node.classList.contains('ct-section') || (packages && node.classList.contains('ct-branch'));
  });
  tree.querySelector('[data-tree-level]').addEventListener('click', () => level());
  tree.querySelector('[data-tree-packages]').addEventListener('click', () => level(true));
  tree.querySelector('[data-tree-expand]').addEventListener('click', () => details.forEach(node => {node.open = true;}));
  tree.querySelector('[data-tree-collapse]').addEventListener('click', () => details.forEach(node => {node.open = false;}));
  tree.querySelectorAll('summary input, summary a').forEach(control => control.addEventListener('click', event => event.stopPropagation()));
  const branches = Array.from(tree.querySelectorAll('[data-tree-object]'));
  const ids = Array.from(new Set(branches.map(node => node.dataset.treeObject)));
  const limit = tree.querySelector('[data-tree-limit]');
  const previous = tree.querySelector('[data-tree-prev]');
  const next = tree.querySelector('[data-tree-next]');
  let page = 1;
  try {
    const size = localStorage.getItem('work-tree-page-size');
    if (['25', '50', '100', 'all'].includes(size)) limit.value = size;
  } catch (_) {}
  const render = () => {
    const size = limit.value === 'all' ? Math.max(ids.length, 1) : Number(limit.value);
    const pages = Math.max(1, Math.ceil(ids.length / size));
    page = Math.max(1, Math.min(page, pages));
    const shown = new Set(ids.slice((page - 1) * size, page * size));
    branches.forEach(node => {node.hidden = !shown.has(node.dataset.treeObject);});
    tree.querySelectorAll('.ct-section').forEach(section => {section.hidden = !section.querySelector('.ct-branch:not([hidden]), .ct-packages-direct');});
    tree.querySelectorAll('.ct-object').forEach(object => {object.hidden = !object.querySelector('.ct-section:not([hidden])');});
    tree.querySelector('[data-tree-count]').textContent = ids.length ? `Комплексных объектов: ${shown.size} из ${ids.length}` : 'Работы на строительном объекте';
    tree.querySelector('[data-tree-page]').textContent = `${page} / ${pages}`;
    previous.disabled = page === 1; next.disabled = page === pages;
  };
  limit.addEventListener('change', () => {
    page = 1;
    try {localStorage.setItem('work-tree-page-size', limit.value);} catch (_) {}
    render();
  });
  previous.addEventListener('click', () => {page--;render();});
  next.addEventListener('click', () => {page++;render();});
  level();
  if (saved?.returning) {
    const open = new Set(saved.open);
    details.forEach(node => {node.open = open.has(node.dataset.treeKey);});
    page = Number(saved.page) || 1;
  } else if (tree.hasAttribute('data-tree-reveal')) {
    details.forEach(node => {node.open = true;});
  }
  render();
  if (saved?.returning) {
    try {sessionStorage.removeItem(key);} catch (_) {}
    requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, Number(saved.scroll) || 0)));
  }
  document.querySelectorAll('[data-catalogue-page] a[href]').forEach(link => link.addEventListener('click', () => {
    try {sessionStorage.setItem(key, JSON.stringify({returning:true, page, scroll:window.scrollY, open:details.filter(node => node.open).map(node => node.dataset.treeKey)}));} catch (_) {}
  }));
})();
