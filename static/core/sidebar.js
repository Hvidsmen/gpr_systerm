/* Active navigation and accessible mobile drawer. */
document.addEventListener('DOMContentLoaded', function () {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const currentPath = window.location.pathname;
  let activeLink = null;
  let bestMatch = 0;
  sidebar.querySelectorAll('.nav-link').forEach(function (link) {
    const href = new URL(link.href, window.location.origin).pathname;
    const paths = [href, ...(link.dataset.paths || '').split(/\s+/).filter(Boolean)];
    paths.forEach(function (path) {
      // Match whole path segments, with the home page matching only itself.
      const nested = path !== '/' && currentPath.startsWith(path.endsWith('/') ? path : path + '/');
      if ((currentPath === path || nested) && path.length > bestMatch) {
        activeLink = link;
        bestMatch = path.length;
      }
    });
  });
  if (activeLink) {
    activeLink.classList.add('active');
    activeLink.setAttribute('aria-current', 'page');
    let group = activeLink.closest('details');
    while (group) {
      group.open = true;
      group.classList.add('has-active');
      group = group.parentElement.closest('details');
    }
    // Keep the selected item visible without moving the content's scroll position.
    const item = activeLink.getBoundingClientRect(), panel = sidebar.getBoundingClientRect();
    if (item.bottom > panel.bottom) sidebar.scrollTop += item.bottom - panel.bottom + 12;
  }

  const toggle = document.getElementById('sidebarToggle');
  const overlay = document.getElementById('sidebarOverlay');
  if (!toggle || !overlay) return;
  const mobile = window.matchMedia('(max-width: 992px)');
  function visibleControls() {
    return Array.from(sidebar.querySelectorAll('a, summary')).filter(function (el) {
      if (!el.getClientRects().length) return false;
      let group = el.parentElement.closest('details');
      while (group) {
        if (!group.open && el !== group.querySelector(':scope > summary')) return false;
        group = group.parentElement.closest('details');
      }
      return true;
    });
  }
  function setOpen(open, restoreFocus) {
    open = mobile.matches && open;
    sidebar.classList.toggle('show', open);
    overlay.classList.toggle('show', open);
    document.body.classList.toggle('sidebar-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
    sidebar.inert = mobile.matches && !open;
    if (open) {
      const first = visibleControls()[0];
      if (first) first.focus({preventScroll: true});
    } else if (restoreFocus) toggle.focus({preventScroll: true});
  }
  toggle.addEventListener('click', () => setOpen(!sidebar.classList.contains('show'), true));
  overlay.addEventListener('click', () => setOpen(false, true));
  document.addEventListener('keydown', function (event) {
    if (!mobile.matches || !sidebar.classList.contains('show')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false, true);
    } else if (event.key === 'Tab') {
      const controls = [toggle, ...visibleControls()];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !controls.includes(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
  });
  mobile.addEventListener('change', () => setOpen(false, false));
  setOpen(false, false);
});
