(() => {
  const cards = [...document.querySelectorAll('.review-work')];
  const search = document.getElementById('review-search');
  if (!search) return;
  const status = document.getElementById('review-status');
  const section = document.getElementById('review-section');
  const kind = document.getElementById('review-kind');
  const all = document.getElementById('review-select-all');
  const boxes = cards.map(card => card.querySelector('.review-select')).filter(Boolean);
  function counts() {
    const shown = cards.filter(card => !card.hidden);
    const visibleBoxes = shown.map(card => card.querySelector('.review-select')).filter(Boolean);
    document.getElementById('review-visible').textContent = `Показано: ${shown.length} из ${cards.length}`;
    const counter = document.getElementById('review-selected');
    if (counter) counter.textContent = `Выбрано: ${boxes.filter(box => box.checked).length}`;
    if (all) {
      const checked = visibleBoxes.filter(box => box.checked).length;
      all.checked = checked > 0 && checked === visibleBoxes.length;
      all.indeterminate = checked > 0 && checked < visibleBoxes.length;
    }
  }
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    cards.forEach(card => {
      card.hidden = !card.dataset.name.toLocaleLowerCase().includes(query)
        || (status.value && card.dataset.status !== status.value)
        || (section.value && card.dataset.section !== section.value)
        || (kind.value && card.dataset.kind !== kind.value);
      card.style.display = card.hidden ? 'none' : '';
      const box = card.querySelector('.review-select');
      if (box && card.hidden) box.checked = false;
    });
    counts();
  }
  [search, status, section, kind].forEach(field => field.addEventListener('input', filter));
  document.getElementById('review-reset').addEventListener('click', () => {
    [search, status, section, kind].forEach(field => { field.value = ''; });
    filter();
  });
  boxes.forEach(box => {
    box.addEventListener('click', event => event.stopPropagation());
    box.addEventListener('change', counts);
  });
  if (all) all.addEventListener('change', () => {
    cards.filter(card => !card.hidden).forEach(card => { card.querySelector('.review-select').checked = all.checked; });
    counts();
  });
  document.querySelectorAll('.review-pair').forEach(button => button.addEventListener('click', () => {
    [search, status, section, kind].forEach(field => { field.value = ''; });
    filter();
    boxes.forEach(box => { box.checked = [button.dataset.first, button.dataset.second].includes(box.value); });
    counts();
    document.getElementById('review-bulk').scrollIntoView({behavior: 'smooth', block: 'center'});
  }));
  filter();
})();
