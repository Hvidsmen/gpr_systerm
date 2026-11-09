(() => {
 const section=document.querySelector('[data-assignment-initial]');if(!section)return;
 const employee=document.getElementById('id_employee'),state=section.querySelector('[data-initial-state]'),arrival=section.querySelector('[data-initial-arrival]'),departure=section.querySelector('[data-initial-departure]');
 const assigned=document.getElementById('id_assigned_from'),today=arrival.max;
 function update(){
  const allowed=Boolean(employee.value);state.disabled=!allowed;
  const mode=allowed?state.value:'';
  for(const [input,visible,required] of [[arrival,Boolean(mode),mode==='MV'],[departure,mode==='MO',mode==='MO']]){
   input.closest('.app-form-field').hidden=!visible;input.disabled=!visible;input.required=required;input.setAttribute('aria-required',String(required));
   input.max=assigned.value&&assigned.value<today?assigned.value:today;
  }
 }
 employee.addEventListener('change',update);state.addEventListener('change',update);assigned.addEventListener('change',update);update();
})();
