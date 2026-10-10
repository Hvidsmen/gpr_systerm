(() => {
  const object = document.getElementById('id_construction_object');
  const sheet = document.getElementById('id_sheet_name');
  if (!object || !sheet) return;
  const name = () => object.value ? object.selectedOptions[0].textContent.trim() : '';
  let previous = name();
  if (!sheet.value) sheet.value = previous;
  object.addEventListener('change', () => {
    if (!sheet.value || sheet.value === previous) sheet.value = name();
    previous = name();
  });
})();
(() => {
  const file = document.getElementById('id_file');
  const root = document.getElementById('fact-file-periods');
  if (!file || !root) return;
  function period(name) {
    const match = name.match(/(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{4})/);
    if (!match) return null;
    const month = Number(match[2]), year = Number(match[3]), day = Number(match[1]);
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    if (month < 1 || month > 12 || day < 1 || day > last) return null;
    const prefix = `${year}-${String(month).padStart(2, '0')}`;
    return [prefix + '-01', prefix + '-' + last];
  }
  const saved = new Map();
  const render = () => {
    root.querySelectorAll('[data-file-key]').forEach(row => saved.set(row.dataset.fileKey,
      Array.from(row.querySelectorAll('input')).map(input => input.value)));
    root.replaceChildren();
    Array.from(file.files).forEach((item, index) => {
      const row = document.createElement('div'); row.className = 'row g-2 border rounded p-2 mb-2';
      const title = document.createElement('strong'); title.textContent = item.name; row.append(title);
      const fileKey = JSON.stringify([item.name, item.size, item.lastModified]); row.dataset.fileKey = fileKey;
      const old = saved.get(fileKey);
      const dates = (old && old.slice(0, 2)) || period(item.name) || [document.getElementById('id_start').value, document.getElementById('id_end').value];
      [['file_start','Начало периода','date',dates[0]],['file_end','Конец периода','date',dates[1]],['file_sheet','Лист Excel','text',(old ? old[2] : document.getElementById('id_sheet_name').value)]].forEach(([name,label,type,value]) => {
        const wrap = document.createElement('label'); wrap.className = 'col-md-3'; wrap.textContent = label;
        const input = document.createElement('input'); input.name = name; input.type = type; input.value = value; input.required = true; input.className = 'form-control'; wrap.append(input); row.append(wrap);
      });
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-outline-danger col-md-2 align-self-end'; remove.textContent = 'Удалить';
      remove.addEventListener('click', () => {
        const transfer = new DataTransfer(); Array.from(file.files).forEach((entry, i) => {if (i !== index) transfer.items.add(entry);}); file.files = transfer.files; render();
      }); row.append(remove); root.append(row);
    });
  };
  file.addEventListener('change', render);
})();
