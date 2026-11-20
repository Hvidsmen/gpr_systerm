(() => {
  'use strict';
  const form=document.querySelector('[data-project-location-selector]');
  if(!form)return;
  const project=form.elements.project,camp=form.elements.camp;
  project?.addEventListener('change',()=>{
    if(camp)camp.value='';
    form.requestSubmit();
  });
})();
