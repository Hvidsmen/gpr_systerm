(() => {
  const panel = document.getElementById('staging-update');
  if (!panel) return;
  const message = panel.querySelector('[data-test-status]');
  const check = panel.querySelector('[data-test-check] button');
  const update = panel.querySelector('[data-test-update] button');
  let active = ['queued', 'downloading', 'stopping', 'applying', 'starting'].includes(panel.dataset.state);
  let stopped = false;
  panel.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => {
    // Disable only the submit buttons: the CSRF token and selected version must be posted.
    check.disabled = update.disabled = true;
    message.textContent = form.hasAttribute('data-test-check') ? 'Проверяем тестовую ветку…' : 'Передаём обновление серверу…';
  }));
  async function poll() {
    try {
      const options = {cache: 'no-store'};
      if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') options.signal = AbortSignal.timeout(10000);
      const response = await fetch(panel.dataset.statusUrl, options);
      if (response.redirected || [401, 403].includes(response.status)) {
        message.textContent = 'Войдите владельцем теста, чтобы посмотреть результат обновления.';
        stopped = true;
        return;
      }
      if (!response.ok) throw new Error('unavailable');
      const status = await response.json();
      active = active || status.busy;
      panel.querySelector('[data-test-current]').textContent = status.version.slice(0, 12);
      panel.querySelector('[data-test-available]').textContent = status.available.slice(0, 12) || 'Ещё не проверена';
      message.textContent = status.message + (status.available === status.version ? ' Новых обновлений нет.' : '');
      message.className = `alert ${status.state === 'done' ? 'alert-success' : ['error', 'recovery'].includes(status.state) ? 'alert-danger' : 'alert-info'}`;
      check.disabled = status.busy;
      // A signed version token is rendered on the check response, not manufactured in JS.
      update.disabled = !status.can_update || !panel.querySelector('[name="version_token"]').value;
      if (active && !status.busy && status.version !== panel.dataset.version) {
        location.reload();
        return;
      }
    } catch (_) {
      message.textContent = 'Тест временно недоступен. Ожидаем завершения обновления и восстановления связи…';
      check.disabled = update.disabled = true;
    } finally {
      if (!stopped) setTimeout(poll, active ? 3000 : 15000);
    }
  }
  setTimeout(poll, 1500);
})();
