(() => {
  const cards = [...document.querySelectorAll('.review-work')];
  const search = document.getElementById('review-search');
  if (!search) return;
  const status = document.getElementById('review-status');
  const section = document.getElementById('review-section');
  const kind = document.getElementById('review-kind');
  const all = document.getElementById('review-select-all');
  const boxes = cards.map(card => card.querySelector('.review-select')).filter(Boolean);
  const groups=[...document.querySelectorAll('[data-review-group]')];
  function counts() {
    const shown = cards.filter(card => !card.hidden);
    const visibleBoxes = shown.map(card => card.querySelector('.review-select')).filter(Boolean);
    document.getElementById('review-visible').textContent = `Показано: ${shown.length} из ${cards.length}`;
    groups.forEach(group=>{
      const members=cards.filter(card=>group.contains(card)),visible=members.filter(card=>!card.hidden);
      group.hidden=visible.length===0;
      const caption=group.querySelector(':scope > summary [data-review-group-count]');
      if(caption)caption.textContent='Проверено '+visible.filter(card=>card.dataset.status==='checked').length+' из '+visible.length+(visible.length!==members.length?' · по фильтру':'');
      const select=group.querySelector(':scope > summary [data-review-group-select]');
      if(select){const checkboxes=visible.map(card=>card.querySelector('.review-select')).filter(Boolean),checked=checkboxes.filter(box=>box.checked).length;select.checked=checked>0&&checked===checkboxes.length;select.indeterminate=checked>0&&checked<checkboxes.length;}
    });
    const counter = document.getElementById('review-selected');
    if (counter) counter.textContent = `Выбрано: ${boxes.filter(box => box.checked).length}`;
    const selected=boxes.filter(box=>box.checked).length,bulk=document.getElementById('review-bulk');
    if(bulk){bulk.hidden=selected===0;bulk.querySelectorAll('button[value=bulk_merge],button[value=composite_setup]').forEach(button=>button.disabled=selected<2);}
    document.getElementById('review-empty').hidden=shown.length!==0;
    if (all) {
      const checked = visibleBoxes.filter(box => box.checked).length;
      all.checked = checked > 0 && checked === visibleBoxes.length;
      all.indeterminate = checked > 0 && checked < visibleBoxes.length;
    }
  }
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    cards.forEach(card => {
      card.hidden = !card.dataset.name.toLocaleLowerCase().includes(query)
        || (status.value && card.dataset.status !== status.value)
        || (section.value && card.dataset.section !== section.value)
        || (kind.value && card.dataset.kind !== kind.value);
      card.style.display = card.hidden ? 'none' : '';
      const box = card.querySelector('.review-select');
      if (box && card.hidden) box.checked = false;
    });
    counts();
    if(query||status.value||section.value||kind.value)groups.filter(group=>!group.hidden).forEach(group=>group.open=true);
    if(query)cards.filter(card=>!card.hidden).forEach(card=>card.open=true);
  }
  [search, status, section, kind].forEach(field => field.addEventListener('input', filter));
  document.getElementById('review-reset').addEventListener('click', () => {
    [search, status, section, kind].forEach(field => { field.value = ''; });
    filter();
  });
  document.querySelectorAll('[data-review-expand]').forEach(button=>button.addEventListener('click',()=>{
    const level=button.dataset.reviewExpand;
    document.querySelectorAll('[data-review-level]').forEach(node=>node.open=level==='all'||level==='objects'&&node.dataset.reviewLevel==='section'||level==='packages'&&['section','object'].includes(node.dataset.reviewLevel));
  }));
  document.querySelectorAll('[data-review-group-select]').forEach(box=>{
    box.addEventListener('click',event=>event.stopPropagation());
    box.addEventListener('change',()=>{cards.filter(card=>!card.hidden&&box.closest('[data-review-group]').contains(card)).forEach(card=>{const child=card.querySelector('.review-select');if(child)child.checked=box.checked;});counts();});
  });
  boxes.forEach(box => {
    box.addEventListener('click', event => event.stopPropagation());
    box.addEventListener('change', counts);
  });
  if (all) all.addEventListener('change', () => {
    const wanted=all.checked;cards.filter(card => !card.hidden).forEach(card => { const box=card.querySelector('.review-select');if(box)box.checked=wanted; });
    counts();
  });
  document.querySelectorAll('.review-pair').forEach(button => button.addEventListener('click', () => {
    [search, status, section, kind].forEach(field => { field.value = ''; });
    filter();
    document.querySelectorAll('[data-review-expand]').forEach(button=>button.addEventListener('click',()=>{
    const level=button.dataset.reviewExpand;
    document.querySelectorAll('[data-review-level]').forEach(node=>node.open=level==='all'||level==='objects'&&node.dataset.reviewLevel==='section'||level==='packages'&&['section','object'].includes(node.dataset.reviewLevel));
  }));
  document.querySelectorAll('[data-review-group-select]').forEach(box=>{
    box.addEventListener('click',event=>event.stopPropagation());
    box.addEventListener('change',()=>{cards.filter(card=>!card.hidden&&box.closest('[data-review-group]').contains(card)).forEach(card=>{const child=card.querySelector('.review-select');if(child)child.checked=box.checked;});counts();});
  });
  boxes.forEach(box => { box.checked = [button.dataset.first, button.dataset.second].includes(box.value); });
    counts();
    document.getElementById('review-bulk').scrollIntoView({behavior: 'smooth', block: 'center'});
  }));
  document.querySelectorAll('.review-catalog').forEach(button => button.addEventListener('click', () => {
    [search, status, section, kind].forEach(field => { field.value = ''; });
    filter();
    const box = boxes.find(box => box.value === button.dataset.source);
    if (!box) return;
    const card = box.closest('.review-work');
    const select = card.querySelector('select[name="replacement"]');
    if (!select) return;
    select.value = button.dataset.target;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    card.open = true;for(let node=card.parentElement;node;node=node.parentElement)if(node.matches('[data-review-group]'))node.open=true;
    select.closest('details').open = true;
    card.scrollIntoView({behavior: 'smooth', block: 'center'});
  }));
  filter();
})();
