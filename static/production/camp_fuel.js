(()=>{'use strict';const form=document.querySelector('[data-camp-fuel-form]');if(!form)return;
 const preview=form.querySelector('[data-fuel-preview]'),openingMass=form.querySelector('[data-fuel-opening-mass]'),closingMass=form.querySelector('[data-fuel-closing-mass]');
 function read(name,places){const raw=form.elements[name].value.trim().replace(',','.');if(!/^\d+(?:\.\d+)?$/.test(raw))return null;const [whole,part='']=raw.split('.');if(part.length>places)return null;return BigInt(whole)*10n**BigInt(places)+BigInt(part.padEnd(places,'0'));}
 function format(value,unit){if(value===null)return '—';const negative=value<0n,absolute=negative?-value:value;return (negative?'−':'')+(absolute/1000n).toLocaleString('ru-RU')+','+String(absolute%1000n).padStart(3,'0')+' '+unit;}
 function mass(volume,density){if(volume===null||density===null||density<=0n)return null;const value=volume*density,negative=value<0n;return (negative?-1n:1n)*(((negative?-value:value)+500000n)/1000000n);}
 function update(){const opening=read('opening',3),received=read('received',3),consumed=read('consumed',3),closing=[opening,received,consumed].some(v=>v===null)?null:opening+received-consumed;
 preview.textContent=format(closing,'м³');preview.classList.toggle('text-danger',closing!==null&&closing<0n);
 openingMass.textContent=format(mass(opening,read('opening_density',6)),'т');closingMass.textContent=format(mass(closing,read('closing_density',6)),'т');closingMass.classList.toggle('text-danger',closing!==null&&closing<0n);}
 form.addEventListener('input',update);form.addEventListener('change',update);update();
})();
