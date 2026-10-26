(() => {
  const page = document.querySelector('[data-catalogue-page]');
  if (!page) return;
  const closeRows = () => page.querySelectorAll('[data-row-menu]').forEach(button => {
    button.setAttribute('aria-expanded', 'false');
    document.getElementById(button.getAttribute('aria-controls')).hidden = true;
  });
  page.querySelectorAll('[data-row-menu]').forEach(button => button.addEventListener('click', event => {
    event.preventDefault(); event.stopPropagation();
    const panel = document.getElementById(button.getAttribute('aria-controls'));
    const opening = panel.hidden;
    closeRows();
    if (!opening) return;
    page.querySelectorAll('.catalogue-menu[open]').forEach(menu => { menu.open = false; });
    panel.hidden = false; button.setAttribute('aria-expanded', 'true');
    const rect = button.getBoundingClientRect();
    const width = panel.getBoundingClientRect().width;
    panel.style.left = `${Math.max(8, Math.min(rect.right - width, innerWidth - width - 8))}px`;
    panel.style.top = `${Math.max(8, rect.bottom + 4 + panel.offsetHeight > innerHeight ? rect.top - panel.offsetHeight - 4 : rect.bottom + 4)}px`;
  }));
  document.addEventListener('click', event => {
    if (!event.target.closest('.catalogue-row-menu')) closeRows();
    page.querySelectorAll('.catalogue-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const current = page.querySelector('[data-row-menu][aria-expanded=true]');
    closeRows(); current?.focus();
    page.querySelectorAll('.catalogue-menu[open]').forEach(menu => {menu.open = false; menu.querySelector('summary').focus();});
  });
  window.addEventListener('scroll', closeRows, {passive:true});
  window.addEventListener('resize', closeRows);
  page.querySelector('[data-catalogue-select]')?.addEventListener('click', () => {
    document.querySelector('#bulk-delete-form [data-bulk-select]')?.click();
  });
  const filters = page.querySelector('#work-list-filters');
  if (filters) {
    const hierarchy = ['project', 'construction_object', 'section', 'complex_object', 'package', 'selected_work'];
    hierarchy.forEach((name, index) => filters.elements[name]?.addEventListener('change', () => {
      hierarchy.slice(index + 1).forEach(child => {filters.elements[child].value = '';});
      filters.requestSubmit();
    }));
  }
  // Restore a table's scroll position only when returning from one of its links.
  if (!page.querySelector('.catalogue-tree') && page.querySelector('.catalogue-list-table')) {
      const key = `work-catalogue-scroll:${location.pathname}${location.search}`;
      try {
        const saved = sessionStorage.getItem(key);
        if (saved !== null) {sessionStorage.removeItem(key); requestAnimationFrame(() => window.scrollTo(0, Number(saved)));}
        page.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', () => sessionStorage.setItem(key, String(window.scrollY))));
      } catch (_) { /* Storage is optional. */ }
  }
  const forms = Array.from(page.querySelectorAll('[data-catalogue-edit]'));
  let submitting = false;
  forms.forEach(form => {
    const states = page.querySelectorAll('[data-save-state]');
    const snapshot = target => JSON.stringify(Array.from(new FormData(target).entries()).filter(([key]) => key !== 'csrfmiddlewaretoken'));
    // Compare with the server-rendered defaults even if input preceded script loading.
    let initial = snapshot(form);
    if (form.matches('[data-cost-shares]')) {
      const defaults = form.cloneNode(true); defaults.reset();
      initial = snapshot(defaults);
    }
    const hasErrors = !!form.querySelector('.errorlist');
    const update = () => {
      const dirty = snapshot(form) !== initial;
      form.dataset.dirty = String(dirty);
      states.forEach(state => {state.textContent = dirty ? 'Есть изменения' : hasErrors ? 'Есть ошибки' : form.hasAttribute('data-new-record') ? 'Не сохранено' : 'Сохранено'; state.dataset.state = dirty ? 'dirty' : hasErrors ? 'error' : 'saved';});
      if (!form.matches('[data-cost-shares]')) return;
      let total = 0, valid = true;
      form.querySelectorAll('[data-cost-share]').forEach(input => {
        const raw = input.value.trim().replace(/\s/g, '').replace(',', '.');
        const value = Number(raw);
        if (!raw || !Number.isFinite(value) || value < 0 || value > 100) valid = false;
        else total += value;
      });
      const complete = valid && Math.abs(total - 100) < 0.00001;
      page.querySelectorAll('[data-cost-share-total]').forEach(label => {label.textContent = valid ? total.toLocaleString('ru-RU', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';});
      page.querySelectorAll('[data-share-status]').forEach(label => {label.dataset.valid = String(complete);});
      page.querySelectorAll('[data-share-hint]').forEach(label => {label.hidden = complete;});
    };
    form.addEventListener('input', update); form.addEventListener('change', update);
    form.addEventListener('submit', () => {
      submitting = true;
      states.forEach(state => {state.textContent = 'Сохранение…';state.dataset.state = 'saving';});
    });
    update();
  });
  window.addEventListener('beforeunload', event => {
    if (!submitting && forms.some(form => form.dataset.dirty === 'true')) {event.preventDefault();event.returnValue = '';}
  });
})();
