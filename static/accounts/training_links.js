/* Share the permanent production entry URL; never share training tickets. */
(()=>{
 function legacyCopy(text){
  const area=document.createElement('textarea'),previous=document.activeElement;
  area.value=text;area.readOnly=true;area.tabIndex=-1;area.setAttribute('aria-hidden','true');
  Object.assign(area.style,{position:'fixed',top:'0',left:'0',width:'1px',height:'1px',opacity:'0'});
  document.body.append(area);
  try{area.focus({preventScroll:true});area.select();area.setSelectionRange(0,text.length);return document.execCommand('copy');}
  catch(_){return false;}
  finally{area.remove();previous?.focus({preventScroll:true});}
 }
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-training-copy-link]');if(!button)return;
  const cell=button.closest('td'),status=cell.querySelector('[data-training-copy-status]'),manual=cell.querySelector('[data-training-link-manual]'),input=cell.querySelector('[data-training-link-value]'),url=button.dataset.trainingCopyLink;
  button.disabled=true;status.textContent='Копирование…';
  try{
   let copied=false;
   if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(url);copied=true;}catch(_){/* Internal HTTP or browser permission: try the selection API. */}}
   if(!copied)copied=legacyCopy(url);
   if(copied){manual.hidden=true;status.textContent='Ссылка скопирована. Можно отправить участникам.';}
   else{manual.hidden=false;status.textContent='Скопируйте выделенную ссылку вручную: Ctrl+C или через меню копирования.';input.focus({preventScroll:true});input.select();input.setSelectionRange(0,input.value.length);}
  }finally{button.disabled=false;if(manual.hidden)button.focus({preventScroll:true});}
 });
})();
