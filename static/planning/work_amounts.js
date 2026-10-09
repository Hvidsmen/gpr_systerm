const allocationWorkPrices = JSON.parse(document.getElementById('work-prices').textContent);
const allocationMoney = new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB'});
function updateWorkAmount(row) {
  const work = row.querySelector('[name$="-work"]');
  if (!work) return;
  let output = row.querySelector('[data-work-amount]');
  if (!output) {
    output = document.createElement('p');
    output.dataset.workAmount = '';
    output.setAttribute('aria-live', 'polite');
    row.querySelector('.allocation-grid').append(output);
  }
  const price = Number(allocationWorkPrices[work.value]);
  const input = row.querySelector('[name$="-quantity"]');
  const quantity = Number(input.value.replace(/\s/g, '').replace(',', '.'));
  const valid = input.value.trim() !== '' && Number.isFinite(quantity) && quantity >= 0;
  output.hidden = !(Number.isFinite(price) && price > 0);
  output.textContent = output.hidden ? '' : 'Сумма: ' + (valid ? allocationMoney.format(quantity * price) : '—') + ' · Цена: ' + allocationMoney.format(price) + ' за единицу';
  output.title = 'Объём × цена, действующая на начало выбранного месяца';
}
