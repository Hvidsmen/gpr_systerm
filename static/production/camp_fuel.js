(()=>{'use strict';const form=document.querySelector('[data-camp-fuel-form]');if(!form)return;
 const preview=form.querySelector('[data-fuel-preview]'),read=name=>Number(form.elements[name].value.replace(',','.'));
 function update(){const values=['opening','received','consumed'].map(read),balance=values[0]+values[1]-values[2];preview.textContent=values.every(Number.isFinite)?balance.toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' л':'—';preview.classList.toggle('text-danger',balance<0);}
 form.addEventListener('input',update);update();
})();
