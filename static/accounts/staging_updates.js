(() => {
  'use strict';
  const panel = document.getElementById('staging-update');
  if (!panel) return;
  const message = panel.querySelector('[data-test-status]');
  const check = panel.querySelector('[data-test-check] button');
  const update = panel.querySelector('[data-test-update] button');
  const refresh = panel.querySelector('[data-test-refresh] button');
  const confirm = panel.querySelector('[data-test-confirm]');
  const login = panel.querySelector('[data-test-login]');
  const box = panel.querySelector('[data-test-progress-panel]');
  const track = panel.querySelector('[data-test-progress]');
  const bar = panel.querySelector('[data-test-progress-fill]');
  const connection = panel.querySelector('[data-test-connection]');
  let last = JSON.parse(document.getElementById('staging-update-initial').textContent);
  const initialGeneration = last.data_generation;
  let receivedAt = performance.now(), confirmed = true, stopped = false, running = false, failures = 0, timer;
  let wasActive = Boolean(last.busy), stepsStamp = '';
  function duration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '—';
    seconds = Math.max(0, Math.floor(Number(seconds)));
    const hours = Math.floor(seconds/3600), minutes = Math.floor(seconds/60)%60;
    return (hours ? `${hours} ч ` : '') + (hours || minutes ? `${minutes} мин ` : '') + `${String(seconds%60).padStart(2,'0')} с`;
  }
  function timing() {
    const extra = last.busy ? (performance.now()-receivedAt)/1000 : 0;
    for (const [selector, value] of [['[data-test-elapsed]', last.elapsed_seconds], ['[data-test-stage-elapsed]', last.stage_elapsed_seconds]]) {
      panel.querySelector(selector).textContent = duration(value === null || value === undefined ? null : Number(value)+extra);
    }
  }
  function renderProgress(status) {
    box.classList.remove('is-unconfirmed');
    panel.querySelector('[data-test-stage-time-caption]').textContent = 'На этапе:';
    const value = Math.max(0, Math.min(100, Number(status.progress)||0));
    track.setAttribute('aria-valuenow', String(value));
    track.setAttribute('aria-valuetext', status.state === 'done' ? 'Готово' : status.stage_number ? `Этап ${status.stage_number} из ${status.stage_total}: ${status.stage || status.message}` : status.message);
    bar.style.width = `${value}%`;
    bar.className = `progress-bar ${status.state === 'done' ? 'bg-success' : ['error','recovery'].includes(status.state) ? 'bg-danger' : 'progress-bar-striped'}${status.busy ? ' progress-bar-animated' : ''}`;
    panel.querySelector('[data-test-operation]').textContent = status.operation === 'refresh' ? 'Обновление тестовых данных' : 'Установка версии портала';
    panel.querySelector('[data-test-step]').textContent = status.state === 'done' ? 'Готово' : status.stage_number ? `Этап ${status.stage_number} из ${status.stage_total}${['error','recovery'].includes(status.state) ? ' · не завершён' : ''}` : status.state === 'idle' ? 'Обновление не запущено' : 'Обновление не завершено';
    const stamp = JSON.stringify(status.steps);
    if (stamp !== stepsStamp) {
      stepsStamp = stamp;
      const list = panel.querySelector('[data-test-steps]');list.replaceChildren();
      for (const step of status.steps || []) {
        const item = document.createElement('li');item.className = 'test-update-step is-'+step.state;item.dataset.stage = step.code;
        if (step.state === 'current') item.setAttribute('aria-current','step');
        const marker = document.createElement('span');marker.className = 'test-step-marker';marker.setAttribute('aria-hidden','true');marker.textContent = step.state === 'complete' ? '✓' : step.state === 'failed' ? '!' : String(step.number);
        const label = document.createElement('span');label.textContent = step.label;item.append(marker,label);list.append(item);
      }
    }
    timing();
  }
  function unavailable(text, auth = false) {
    confirmed = false;
    panel.querySelector('[data-test-stage-time-caption]').textContent = 'Последний известный этап:';
    box.classList.add('is-unconfirmed');bar.classList.remove('progress-bar-animated');
    track.setAttribute('aria-valuetext','Последний подтверждённый этап; текущее состояние недоступно.');
    panel.querySelector('[data-test-step]').textContent = last.stage_number ? `Последний подтверждённый этап: ${last.stage_number} из ${last.stage_total}` : 'Состояние не подтверждено';
    message.className = 'alert alert-warning';message.textContent = text;
    connection.textContent = auth ? 'Состояние не подтверждено. Войдите владельцем теста и откройте обновление заново.' : 'Связь с тестовым порталом потеряна. Ждём его запуска и повторяем проверку автоматически; полоса не продвигается без ответа сервера.';
    check.disabled = update.disabled = refresh.disabled = true;
    if (auth) {login.hidden = false;stopped = true;}
  }
  confirm.addEventListener('change', () => {refresh.disabled = !confirmed || !last.can_refresh || !panel.querySelector('[name="refresh_token"]').value || !confirm.checked;});
  panel.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => {
    check.disabled = update.disabled = refresh.disabled = true;
    message.textContent = form.hasAttribute('data-test-check') ? 'Проверяем тестовую ветку…' : 'Передаём задание серверу…';
  }));
  async function poll() {
    if (running || stopped) return;
    running = true;clearTimeout(timer);
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),10000);
    try {
      const response = await fetch(panel.dataset.statusUrl,{cache:'no-store', credentials:'same-origin', signal:controller.signal});
      if (response.redirected || [401,403].includes(response.status)) {
        unavailable('Войдите владельцем теста, чтобы посмотреть результат обновления. Последний показанный этап больше не подтверждён.',true);return;
      }
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('unavailable');
      const status = await response.json();
      if (!Array.isArray(status.steps) || typeof status.busy !== 'boolean') throw new Error('unavailable');
      last = status;receivedAt = performance.now();confirmed = true;failures = 0;wasActive = wasActive || status.busy;
      panel.querySelector('[data-test-current]').textContent = status.version.slice(0,12);
      panel.querySelector('[data-test-available]').textContent = status.available.slice(0,12) || 'Ещё не проверена';
      message.textContent = status.message + (status.available && status.available === status.version ? ' Новых обновлений нет.' : '');
      message.className = `alert ${status.state === 'done' ? 'alert-success' : ['error','recovery'].includes(status.state) ? 'alert-danger' : 'alert-info'}`;
      connection.textContent = status.busy ? 'Обновление выполняется. Состояние проверяется автоматически.' : 'Состояние подтверждено сервером.';
      login.hidden = true;renderProgress(status);
      check.disabled = status.busy;
      update.disabled = !status.can_update || !panel.querySelector('[name="version_token"]').value || status.available !== panel.dataset.available;
      refresh.disabled = !status.can_refresh || !panel.querySelector('[name="refresh_token"]').value || !confirm.checked;
      if (wasActive && status.state === 'done' && (status.version !== panel.dataset.version || status.data_generation !== initialGeneration)) {location.reload();return;}
    } catch (_) {
      failures++;
      unavailable('Тест временно недоступен. Ожидаем завершения обновления и восстановления связи…');
    } finally {
      clearTimeout(timeout);running = false;
      if (!stopped) timer = setTimeout(poll, failures ? Math.min(15000,3000*2**Math.min(failures-1,3)) : last.busy ? 3000 : 15000);
    }
  }
  renderProgress(last);setInterval(timing,1000);
  addEventListener('online', () => {if (!stopped) {clearTimeout(timer);poll();}});
  timer = setTimeout(poll,1500);
})();
