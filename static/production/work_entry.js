/* Keep the dated work heading visible when adding another fact row. */
(() => {
  const source = document.getElementById('production-work-choices');
  const host = document.querySelector('[data-rows="works"]');
  if (!source || !host) return;
  const catalogue = new Map(JSON.parse(source.textContent).map(choice => [choice.token, choice.display]));
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:6});
  function refresh(row) {
    const work = row.querySelector('[name$="-project_work"]'), item = row.querySelector('[name$="-work_item"]');
    const display = catalogue.get(item?.value ? `item:${work?.value}:${item.value}` : `work:${work?.value}`);
    const heading = row.querySelector('[data-work-heading]');
    if (!heading) return;
    if (!display) {
      if (work?.tagName === 'SELECT') heading.hidden = true;
      return;
    }
    heading.hidden = false;row.dataset.planned=display.has_plan?'1':'0';const remove=row.querySelector('[data-work-remove]');if(remove)remove.hidden=display.has_plan;
    row.querySelector('[data-work-title]').textContent = display.title;
    row.querySelector('[data-work-path]').textContent = display.path;
    row.querySelector('[data-work-unplanned]').hidden = display.has_plan;
    row.querySelector('[data-work-plan]').textContent = display.has_plan ? `${number.format(display.plan)} ${display.unit}` : '—';
    row.querySelector('[data-work-previous]').textContent = `${number.format(display.previous)} ${display.unit}`;
  }
  function refreshAll(){host.querySelectorAll('[data-work-fact-row]').forEach(refresh);}
  refreshAll();host.closest('form').addEventListener('production-live-applied',refreshAll);
  host.addEventListener('change', event => {
    const row = event.target.closest('[data-work-fact-row]');
    if (row) refresh(row);
  });
  host.addEventListener('click', event => {
    const expand = event.target.closest('[data-work-expand]'), collapse = event.target.closest('[data-work-collapse]');
    if (expand || collapse) host.querySelectorAll('[data-work-object], [data-work-package]').forEach(group => { group.open = Boolean(expand); });
  });
})();
