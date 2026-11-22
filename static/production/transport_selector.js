(() => {
  'use strict';
  const form=document.querySelector('[data-object-location-selector]');
  if(!form)return;
  const constructionObject=form.elements.construction_object,camp=form.elements.camp;
  constructionObject?.addEventListener('change',()=>{
    if(camp)camp.value='';
    form.requestSubmit();
  });
})();
