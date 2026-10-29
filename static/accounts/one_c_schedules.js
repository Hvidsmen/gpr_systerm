(() => {
 'use strict';
 document.querySelectorAll('[data-schedule-form]').forEach(form => {
  const frequency = form.querySelector('[data-schedule-frequency]');
  const update = () => {
   form.querySelectorAll('[data-schedule-modes]').forEach(group => {
    const visible = group.dataset.scheduleModes.split(' ').includes(frequency.value);
    group.hidden = !visible;
    group.querySelectorAll('input,select').forEach(field => {
     field.disabled = !visible;
     field.required = visible;
    });
   });
  };
  frequency.addEventListener('change', update);
  update();
 });
})();
