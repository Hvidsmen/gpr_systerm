(() => {
  const panel = document.getElementById('portal-update');
  if (!panel) return;
  const message = panel.querySelector('[data-update-message]');
  const stage = panel.querySelector('[data-update-stage]');
  const box = panel.querySelector('[data-update-box]');
  const button = panel.querySelector('[data-update-submit]');
  let stopped = false;
  panel.querySelector('form').addEventListener('submit', () => {
    button.disabled = true;
    message.textContent = 'Передаём задание обновления серверу…';
  });
  async function refresh() {
    try {
      const options = {cache: 'no-store'};
      if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') options.signal = AbortSignal.timeout(10000);
      const response = await fetch(panel.dataset.statusUrl, options);
      if (response.redirected || [401,403].includes(response.status)) {
        message.textContent = 'Для просмотра результата войдите в портал как администратор и откройте эту страницу снова.';
        stopped = true;
        return;
      }
      if (!response.ok) throw new Error('Unavailable');
      const status = await response.json();
      panel.querySelector('[data-update-version]').textContent = status.version || 'Не определена';
      message.textContent = status.message;
      stage.textContent = status.stage;
      button.disabled = !status.ready || status.busy;
      box.className = `alert ${status.state === 'done' ? 'alert-success' : status.state === 'error' ? 'alert-danger' : 'alert-info'}`;
    } catch (_) {
      button.disabled = true;
      box.className = 'alert alert-info';
      message.textContent = 'Портал временно недоступен. Ожидаем восстановления связи…';
      stage.textContent = 'Проверка продолжится автоматически. Повторно запускать обновление не нужно.';
    } finally {
      if (!stopped) setTimeout(refresh,3000);
    }
  }
  refresh();
})();
