/* Fill each meeting file's editable month from an unambiguous filename date. */
(() => {
  function periodFromFilename(filename) {
    const dates = [...filename.matchAll(/(?:^|[^0-9])(\d{1,2})([.\-_])(\d{1,2})\2(\d{4})(?!\d)/g)];
    if (!dates.length) return null;
    const periods = new Map();
    for (const match of dates) {
      const day = Number(match[1]);
      const month = Number(match[3]);
      const year = Number(match[4]);
      if (year < 1000 || month < 1 || month > 12) return null;
      const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
      if (day < 1 || day > lastDay) return null;
      const prefix = `${year}-${String(month).padStart(2, '0')}`;
      periods.set(prefix, {start: `${prefix}-01`, end: `${prefix}-${lastDay}`});
    }
    return periods.size === 1 ? [...periods.values()][0] : null;
  }

  function applyFilePeriod(filename, start, end) {
    const period = periodFromFilename(filename);
    if (!period || !start || !end) return false;
    start.value = period.start;
    end.value = period.end;
    return true;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {periodFromFilename, applyFilePeriod};
  }
  if (typeof document === 'undefined') return;
  document.querySelectorAll('form[data-meeting-period]').forEach(form => {
    // Delegation also handles file rows added after the initial page load.
    form.addEventListener('change', event => {
      const input = event.target;
      if (input.type !== 'file') return;
      const row = input.closest('#meeting-files tr');
      const scope = row || form;
      const start = scope.querySelector('input[name="start"], input[name$="-start"]');
      const end = scope.querySelector('input[name="end"], input[name$="-end"]');
      let note = input.parentElement.querySelector('[data-meeting-period-note]');
      if (!note) {
        note = document.createElement('div');
        note.dataset.meetingPeriodNote = '';
        note.className = 'small text-muted mt-1';
        note.setAttribute('role', 'status');
        input.parentElement.appendChild(note);
      }
      const file = input.files[0];
      if (!file) {
        note.textContent = '';
      } else if (applyFilePeriod(file.name, start, end)) {
        const display = value => value.split('-').reverse().join('.');
        note.textContent = `Период по названию файла: ${display(start.value)} — ${display(end.value)}. Даты можно изменить.`;
      } else {
        note.textContent = 'В названии нет однозначной корректной даты. Укажите период вручную.';
      }
    });
  });
})();
