(function () {
  const panel = document.querySelector('[data-uat-progress-panel]');
  if (!panel) return;
  const track = panel.querySelector('[data-uat-progress]');
  const fill = panel.querySelector('[data-uat-progress-fill]');
  const stepLabel = panel.querySelector('[data-uat-step]');
  const statusLabel = panel.querySelector('[data-uat-status]');
  const initial = JSON.parse(document.getElementById('uat-progress-initial').textContent);
  let wasBusy = Boolean(initial.busy);

  function render(status) {
    const value = Math.max(0, Math.min(100, Number(status.progress) || 0));
    track.setAttribute('aria-valuenow', value);
    track.setAttribute('aria-valuetext', status.stage || '');
    fill.style.width = `${value}%`;
    fill.className = `progress-bar ${status.state === 'done' ? 'bg-success' : status.state === 'error' ? 'bg-danger' : status.busy ? 'progress-bar-striped progress-bar-animated' : ''}`;
    stepLabel.textContent = status.stage_number ? `Этап ${status.stage_number} из ${status.stage_total}` : 'Не запущена';
    statusLabel.textContent = status.stage || '';
    statusLabel.classList.toggle('text-danger', status.state === 'error');
    statusLabel.classList.toggle('text-muted', status.state !== 'error');
    status.steps.forEach((step) => {
      const item = panel.querySelector(`[data-stage="${step.code}"]`);
      if (!item) return;
      item.className = `uat-step is-${step.state}`;
      if (step.state === 'current') item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
      item.querySelector('.uat-step-marker').textContent = step.state === 'complete' ? '✓' : step.state === 'failed' ? '!' : step.number;
    });
  }

  async function poll() {
    try {
      const response = await fetch(panel.dataset.statusUrl, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const status = await response.json();
      render(status);
      if (wasBusy && !status.busy) {
        window.location.reload();
        return;
      }
      wasBusy = wasBusy || Boolean(status.busy);
      if (status.busy) window.setTimeout(poll, 2500);
    } catch (_error) {
      statusLabel.textContent = 'Не удалось обновить статус. Повторная проверка через 5 секунд.';
      window.setTimeout(poll, 5000);
    }
  }

  render(initial);
  if (initial.busy) window.setTimeout(poll, 1200);
}());
