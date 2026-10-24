(() => {
  const panel = document.getElementById('portal-update');
  const notice = document.getElementById('portal-update-notice');
  if (!panel && !notice) return;
  const url = (panel || notice).dataset.statusUrl;
  let stopped = false, wasActive = false, lastStatus = null;
  const dismissKey = 'dismissed-portal-update';
  function dismissed(status) {
    try { return sessionStorage.getItem(dismissKey) === status.created_at; } catch (_) { return false; }
  }
  function progress(root,status) {
    const track = root.querySelector('[data-update-progress]');
    const bar = root.querySelector('[data-update-fill]');
    track.setAttribute('aria-valuenow',status.progress);
    bar.style.width = `${status.progress}%`;
    bar.className = `progress-bar ${status.state === 'done' ? 'bg-success' : status.state === 'error' ? 'bg-danger' : 'progress-bar-striped'}${status.busy ? ' progress-bar-animated' : ''}`;
    root.querySelector('[data-update-step]').textContent = status.state === 'done' ? 'ГОТОВО' : status.state === 'error' ? 'Обновление не завершено' : status.stage_number ? `Этап ${status.stage_number} из ${status.stage_total}: ${status.stage}` : status.stage || 'Ожидание запуска';
  }
  if (panel) panel.querySelector('form').addEventListener('submit', () => {
    panel.querySelector('[data-update-submit]').disabled = true;
    panel.querySelector('[data-update-message]').textContent = 'Передаём задание обновления серверу…';
  });
  if (notice) notice.querySelector('[data-update-dismiss]').addEventListener('click', () => {
    if (lastStatus) try {sessionStorage.setItem(dismissKey,lastStatus.created_at);} catch (_) {}
    notice.hidden = true;
  });
  async function refresh() {
    try {
      const options = {cache: 'no-store'};
      if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') options.signal = AbortSignal.timeout(10000);
      const response = await fetch(url,options);
      if (response.redirected || [401,403].includes(response.status)) {
        if (panel) panel.querySelector('[data-update-message]').textContent = 'Для просмотра результата войдите как администратор.';
        if (notice) notice.hidden = true;
        stopped = true;return;
      }
      if (!response.ok) throw new Error('Unavailable');
      const status = await response.json();lastStatus = status;
      if (status.busy) wasActive = true;
      if (panel) {
        panel.querySelector('[data-update-version]').textContent = status.version || 'Не определена';
        panel.querySelector('[data-update-message]').textContent = status.message;
        panel.querySelector('[data-update-stage]').textContent = status.stage;
        panel.querySelector('[data-update-submit]').disabled = !status.ready || status.busy;
        panel.querySelector('[data-update-box]').className = `alert ${status.state === 'done' ? 'alert-success' : status.state === 'error' ? 'alert-danger' : 'alert-info'}`;
        progress(panel,status);
      }
      if (notice) {
        const recent = status.finished_at && Date.now()-Date.parse(status.finished_at)<300000;
        notice.hidden = Boolean(panel) || !(status.busy || ((wasActive || recent) && ['done','error'].includes(status.state) && !dismissed(status)));
        notice.querySelector('[data-update-notice-message]').textContent = status.message;
        notice.querySelector('[data-update-dismiss]').hidden = status.busy;
        progress(notice,status);
      }
    } catch (_) {
      if (panel) {
        panel.querySelector('[data-update-submit]').disabled = true;
        panel.querySelector('[data-update-box]').className = 'alert alert-info';
        panel.querySelector('[data-update-message]').textContent = 'Портал перезапускается или временно недоступен. Ожидаем восстановления связи…';
      }
      if (notice && wasActive && !panel) {
        notice.hidden = false;
        notice.querySelector('[data-update-notice-message]').textContent = 'Перезапуск портала. Ожидаем восстановления связи…';
      }
    } finally {
      if (!stopped) setTimeout(refresh,panel || lastStatus?.busy ? 3000 : 15000);
    }
  }
  refresh();
})();
