(() => {
  const picker = document.getElementById('ad-group-picker');
  if (!picker) return;
  const query = document.getElementById('ad-group-query');
  const results = document.getElementById('ad-group-results');
  const status = document.getElementById('ad-group-status');
  const button = document.getElementById('ad-group-load');
  const target = document.getElementById('id_ad_group_dn');
  let currentRequest = null;
  async function load() {
    if(!query.value.trim()){status.textContent='Введите название или описание группы для поиска.';return;}
    if (currentRequest) currentRequest.abort();
    const controller = new AbortController();
    currentRequest = controller;
    status.textContent = 'Загрузка групп из AD…';
    results.replaceChildren();
    try {
      const url = new URL(picker.dataset.url, window.location.origin);
      url.searchParams.set('q', query.value.trim());
      const response = await fetch(url, {signal: controller.signal, headers: {'Accept': 'application/json'}});
      if (response.redirected) throw new Error('Сессия завершилась. Войдите в систему заново.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось загрузить группы AD.');
      for (const group of data.groups) {
        const option = new Option(`${group.name} — ${group.dn}${group.description ? ' · ' + group.description : ''}`, group.dn);
        results.add(option);
      }
      results.selectedIndex = -1;
      status.textContent = data.groups.length ? `Найдено: ${data.groups.length}.${data.more ? ' Показаны первые 100 результатов — уточните поиск.' : ''}` : 'Группы не найдены. Попробуйте другое название.';
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = error instanceof SyntaxError ? 'Не удалось получить ответ сервера.' : error.message;
    }
  }
  results.addEventListener('change', () => {
    if (results.selectedIndex < 0) return;
    target.value = results.value;
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
  });
  button.addEventListener('click', load);
  query.addEventListener('keydown', event => {
    if (event.key === 'Enter') {event.preventDefault(); load();}
  });
})();
