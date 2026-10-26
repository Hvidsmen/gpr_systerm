(() => {
  function init(root) {
    if (!root || root.dataset.ready) return;
    root.dataset.ready = 'true';
    const scope = root.querySelector('[name=target_scope]');
    const boxes = Array.from(root.querySelectorAll('input[name=targets]'));
    const locked = root.hasAttribute('data-targets-locked');
    const search = root.querySelector('[data-target-search]');
    const normalized = value => value.toLocaleLowerCase('ru').replace(/ё/g,'е').trim();
    const update = () => {
      const works = scope.value === 'works';
      root.querySelector('[data-target-work-options]').hidden = !works;
      boxes.forEach(box => {box.disabled = locked || !works;});
      root.querySelector('[data-target-count]').textContent = 'Выбрано работ: ' + boxes.filter(box => box.checked).length;
      root.querySelectorAll('[data-target-select-visible],[data-target-clear]').forEach(button => {button.disabled = locked || !works;});
      scope.setCustomValidity(!locked && works && !boxes.some(box => box.checked) ? 'Выберите хотя бы одну работу.' : '');
      const chosen = works ? boxes.filter(box => box.checked).map(box => box.value) : [];
      if (!locked) {
        const parts = chosen[0]?.split(':') || [];
        const work = scope.form?.elements.namedItem('work'), item = scope.form?.elements.namedItem('work_item');
        if (work) work.value = parts[1] || '';
        if (item) item.value = parts[0] === 'item' ? parts[2] : '';
      }
    };
    root.addEventListener('change', update);
    search.addEventListener('input', () => {
      const words = normalized(search.value).split(/\s+/).filter(Boolean);
      boxes.forEach(box => {const label=box.closest('label');label.parentElement.hidden = !words.every(word => normalized(label.textContent).includes(word));});
    });
    search.addEventListener('keydown', event => {if (event.key==='Enter')event.preventDefault();});
    root.querySelector('[data-target-select-visible]').addEventListener('click', () => {
      boxes.filter(box => !box.disabled && !box.closest('label').parentElement.hidden).forEach(box => {box.checked = true;});
      scope.dispatchEvent(new Event('change',{bubbles:true}));
    });
    root.querySelector('[data-target-clear]').addEventListener('click', () => {
      boxes.forEach(box => {if (!box.disabled)box.checked = false;});
      scope.dispatchEvent(new Event('change',{bubbles:true}));
    });
    root.resourceTargetsUpdate = update;
    update();
  }
  function set(root, mode, tokens) {
    root.querySelector('[name=target_scope]').value = mode;
    root.querySelectorAll('input[name=targets]').forEach(box => {box.checked = tokens.includes(box.value);});
    init(root);root.resourceTargetsUpdate();
  }
  window.workResourceTargets = {init,set};
  document.querySelectorAll('[data-resource-target-picker]').forEach(init);
})();
