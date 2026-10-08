(() => {
  function initialize() {
    document.querySelectorAll('[data-price-inheritance]').forEach(panel => {
      const source = document.getElementById(panel.dataset.source);
      const price = document.getElementById(panel.dataset.price);
      const form = source?.form;
      if (!source || !price || !form) return;
      const name = form.querySelector('[name="name"]');
      const unit = form.querySelector('[name="unit"]');
      const day = form.querySelector('[name="effective_from"]');
      const correction = form.querySelector('[name="corrects"]');
      const status = panel.querySelector('[data-price-status]');
      const all = [...source.options].map(option => ({text:option.textContent, value:option.value}));
      let revision = 0, manualPrice = price.value;
      const resetChoices = () => {source.replaceChildren(...all.map(option => new Option(option.text, option.value))); source.dispatchEvent(new Event('change', {bubbles:true}));};
      function current() {
        return {unit: unit?.value || panel.dataset.unit, name:name?.value || panel.dataset.name,
          date:day?.value || '', exclude:panel.dataset.exclude};
      }
      async function lookup(find) {
        const request = ++revision;
        const values = current();
        if (!find && !source.value) {
          price.readOnly=false;price.value=manualPrice;
          status.textContent='Цена вводится вручную.';return;
        }
        if (!values.unit || (find && !values.name.trim())) {
          status.textContent='Сначала укажите название и единицу измерения.';return;
        }
        if (!find) values.source=source.value;
        status.textContent='Поиск цены…';
        if (!find) price.readOnly=true;
        try {
          const response=await fetch(panel.dataset.url+'?'+new URLSearchParams(values), {headers:{Accept:'application/json'}});
          const data=await response.json();
          if (request!==revision) return;
          if (!response.ok) throw new Error(data.error || 'Не удалось получить цену.');
          if (find) {
            if (!data.results.length) {status.textContent='Совпадений по названию и единице измерения нет. Можно выбрать источник вручную из всех работ.';return;}
            source.replaceChildren(new Option('Выберите работу-источник', ''), ...data.results.map(row=>new Option(`${row.label} · ${row.price} ₽`, row.id)));
            if (data.results.length===1) source.value=String(data.results[0].id);
            source.dispatchEvent(new Event('change', {bubbles:true}));
            if (data.results.length>1) status.textContent=`Найдено совпадений: ${data.results.length}. Выберите работу-источник в списке.`;
          } else if (data.results.length===1) {
            price.value=data.results[0].price;price.readOnly=true;
            status.textContent=`Будет скопирована цена ${data.results[0].price} ₽ за ${values.unit}. Источник: ${data.results[0].label}.`;
          } else {
            price.value=manualPrice;price.readOnly=false;
            status.textContent='Источник недоступен или единицы измерения не совпадают. Выберите другую работу.';
          }
        } catch(error) {
          if(request!==revision)return;
          price.readOnly=false;
          status.textContent='Не удалось получить цену. Повторите поиск. '+error.message;
        }
      }
      price.addEventListener('input',()=>{if(!source.value) manualPrice=price.value;});
      source.addEventListener('change',()=>lookup(false));
      day?.addEventListener('change',()=>lookup(false));
      correction?.addEventListener('change',()=>{
        const dates=JSON.parse(correction.dataset.effectiveDates || '{}');
        if(correction.value && dates[correction.value])day.value=dates[correction.value];
        if(day)day.readOnly=Boolean(correction.value);
        lookup(false);
      });
      unit?.addEventListener('change',()=>{revision++;resetChoices();});
      name?.addEventListener('input',()=>{revision++;});
      panel.querySelector('[data-find-price]').addEventListener('click',()=>lookup(true));
      panel.querySelector('[data-all-prices]').addEventListener('click',resetChoices);
      if(source.value)lookup(false);
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initialize);
  else initialize();
})();
