(() => {
  const form = document.getElementById('matrix-filters');
  const matrix = document.getElementById('plan-fact-matrix');
  if (!form) return;
  form.querySelectorAll('[name="start"],[name="end"]').forEach(field => {
    field.addEventListener('change', () => { form.querySelector('[name="month"]').value = ''; });
  });
  form.querySelectorAll('[data-section-toggle]').forEach(toggle => {
    toggle.addEventListener('change', () => {
      form.requestSubmit();
    });
  });
  const consolidated = form.querySelector('[name="consolidated"]');
  const versions = form.querySelectorAll('[name^="version_"]');
  const syncVersions = () => {
    const selected = Boolean(consolidated && consolidated.value);
    versions.forEach(field => { field.disabled = selected; if (selected) field.value = ''; });
  };
  consolidated?.addEventListener('change', () => {
    syncVersions();
    if (!consolidated.value) {
      form.querySelectorAll('[name="start"],[name="end"],[name="month"]').forEach(field => { field.disabled = false; });
    }
    if (consolidated.value) {
      // The selected composition supplies its own period unless the user applies a narrower one later.
      form.querySelector('[name="start"]').disabled = true;
      form.querySelector('[name="end"]').disabled = true;
      form.querySelector('[name="month"]').disabled = true;
      form.querySelector('[name="project"]').value = '';
      const objects = form.querySelector('[name="objects"]');
      if (objects) Array.from(objects.options).forEach(option => { option.selected = false; });
      form.requestSubmit();
    }
  });
  const project = form.querySelector('[name="project"]');
  project?.addEventListener('change', () => {
    if (consolidated) consolidated.value = '';
    versions.forEach(field => { field.value = ''; field.disabled = false; });
    const objects = form.querySelector('[name="objects"]');
    if (objects) Array.from(objects.options).forEach(option => { option.selected = false; });
    form.requestSubmit();
  });
  syncVersions();
  document.querySelectorAll('[data-expand-matrix]').forEach(button => button.addEventListener('click', () => {
    const expanded=button.dataset.expandMatrix==='true';
    matrix?.querySelectorAll('details').forEach(details=>{details.open=expanded;});
    matrix?.querySelectorAll('[data-work-toggle]').forEach(toggle=>{toggle.setAttribute('aria-expanded',String(expanded));toggle.textContent=expanded?'▾':'▸';});
    matrix?.querySelectorAll('[data-work-parent]').forEach(row=>{row.hidden=!expanded;});
  }));
  matrix?.querySelectorAll('[data-work-toggle]').forEach(button => button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? '▾' : '▸';
    matrix.querySelectorAll('[data-work-parent]').forEach(row => {
      if (row.dataset.workParent === button.dataset.workToggle) row.hidden = !expanded;
    });
  }));
})();

(()=>{const grid=document.querySelector('[data-report-calendar]');if(!grid)return;const head=grid.querySelector('[data-report-head-scroll]'),body=grid.querySelector('[data-report-body]'),matrix=grid.querySelector('#plan-fact-matrix');const n=[...head.querySelectorAll('thead tr:first-child th')].slice(1).reduce((sum,th)=>sum+th.colSpan,0);grid.style.setProperty('--report-columns',String(n));function sync(a,b){if(Math.abs(a.scrollLeft-b.scrollLeft)>.5)b.scrollLeft=a.scrollLeft;}head.addEventListener('scroll',()=>sync(head,body),{passive:true});body.addEventListener('scroll',()=>sync(body,head),{passive:true});let queued=false;function layout(){queued=false;const nameWidth=parseFloat(getComputedStyle(grid).getPropertyValue('--report-name'))||380;grid.style.setProperty('--report-value',Math.max(innerWidth<=700?84:88,(grid.clientWidth-nameWidth)/Math.max(1,n))+'px');const action=document.querySelector('.plan-action-header'),heading=grid.querySelector('[data-report-heading]');if(action)heading.style.top=((parseFloat(getComputedStyle(action).top)||0)+action.getBoundingClientRect().height+6)+'px';const origin=matrix.getBoundingClientRect().left;matrix.querySelectorAll('.matrix-scroll,.report-calendar-summary').forEach(el=>{if(!el.getClientRects().length)return;const inset=Math.max(0,el.getBoundingClientRect().left-origin+(parseFloat(el.style.getPropertyValue('--report-inset'))||0));el.style.setProperty('--report-inset',inset+'px');});sync(body,head);}function schedule(){if(!queued){queued=true;requestAnimationFrame(layout);}}window.addEventListener('resize',schedule);matrix.addEventListener('toggle',schedule,true);new MutationObserver(schedule).observe(matrix,{subtree:true,attributes:true,attributeFilter:['hidden','open']});if(window.ResizeObserver){const o=new ResizeObserver(schedule);o.observe(grid);const a=document.querySelector('.plan-action-header');if(a)o.observe(a);}layout();})();
