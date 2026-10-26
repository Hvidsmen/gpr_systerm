/* Excel TSV parsing and atomic pasting into one daily SMG measure. */
(() => {
 'use strict';
 function parseRow(text){
  if(text.length>2*1024*1024)throw new Error('Выберите строку одного месяца. Скопированный диапазон слишком большой.');
  const rows=[[]];let value='',quoted=false;
  for(let i=0;i<text.length;i++){
   const char=text[i];
   if(char==='"'&&(quoted||value==='')){if(quoted&&text[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}
   else if(!quoted&&(char==='\t'||char==='\n'||char==='\r')){
    rows.at(-1).push(value);value='';
    if(char!=='\t'){if(char==='\r'&&text[i+1]==='\n')i++;rows.push([]);}
   }else value+=char;
  }
  if(quoted)throw new Error('Не удалось прочитать строку Excel. Скопируйте значения одной строки заново.');
  rows.at(-1).push(value);
  while(rows.length>1&&rows.at(-1).every(v=>!v.trim()))rows.pop();
  if(rows.length!==1)throw new Error('Для вставки выберите одну строку работы или ресурса в Excel.');
  const cells=rows[0].map(value=>value.trim());let last=cells.length-1;
  while(last>=0&&!cells[last])last--;
  if(last>500)throw new Error('Выберите значения только за нужный месяц.');
  // Whole Excel rows may contain thousands of empty columns. Retain enough
  // trailing blanks for a full month so selected empty days still become zero.
  return cells.slice(0,Math.min(cells.length,last+32));
 }
 function number(value){
  if(/[\r\n\t]/.test(value))throw new Error('В ячейке должен быть один числовой показатель.');
  value=value.trim().replace(/[\s\u00a0\u202f]/g,'').replace(',','.');
  if(!value||/^[—–-]$/.test(value))return '0';
  if(!/^\+?\d+(?:\.\d{1,6})?$/.test(value))throw new Error('Неотрицательное число, до 6 знаков после запятой: «'+value.slice(0,40)+'».');
  return value.replace(/^\+/,'');
 }
 const api={parseRow,number};if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(typeof document==='undefined')return;
 const root=document.getElementById('smg-matrix'),dialog=document.querySelector('[data-smg-paste-dialog]');if(!root||!dialog)return;
 const q=s=>dialog.querySelector(s),status=document.getElementById('smg-save-status'),undo=document.querySelector('[data-smg-paste-undo]');
 let pending=null,lastPatch=null;
 function message(text){if(status)status.textContent=text;}
 function validate(targets,cells){
  if(cells.length>targets.length)throw new Error('Значения не помещаются в оставшиеся дни месяца. Уточните диапазон столбцов Excel.');
  return cells.map((cell,i)=>{
   const value=number(cell),probe=targets[i].cloneNode();probe.value=value;
   if(!probe.checkValidity())throw new Error('День '+targets[i].dataset.date+': число не соответствует точности этого показателя.');
   return value;
  });
 }
 function apply(targets,values){
  lastPatch=values.map((value,i)=>({input:targets[i],before:targets[i].value,after:value}));
  lastPatch.forEach(p=>{p.input.value=p.after;p.input.dispatchEvent(new Event('input',{bubbles:true}));});
  if(undo)undo.hidden=false;
  message('Вставлено дней: '+values.length+'. Есть изменения — нажмите «Сохранить».');targets[0].focus({preventScroll:true});
 }
 function preview(){
  const start=Number(q('[data-paste-first]').value),end=Number(q('[data-paste-last]').value),body=q('[data-paste-preview]');body.replaceChildren();
  const error=q('[data-paste-error]');error.hidden=true;q('[data-paste-apply]').disabled=true;
  try{
   if(end<start)throw new Error('Последний столбец должен идти после первого.');
   const values=validate(pending.targets,pending.cells.slice(start,end+1));
   values.forEach((value,i)=>{const tr=document.createElement('tr');for(const text of [pending.targets[i].dataset.date.split('-').reverse().join('.'),value]){const td=document.createElement('td');td.textContent=text;tr.append(td);}body.append(tr);});
   pending.values=values;q('[data-paste-apply]').disabled=false;
  }catch(e){error.hidden=false;error.textContent=e.message;}
 }
 function openPreview(input,targets,cells){
  pending={input,targets,cells,values:[]};
  const first=q('[data-paste-first]'),last=q('[data-paste-last]');first.replaceChildren();last.replaceChildren();
  cells.forEach((cell,i)=>{const label=(i+1)+' · '+(cell||'пусто').slice(0,65);first.add(new Option(label,String(i)));last.add(new Option(label,String(i)));});
  let start=cells.findIndex(cell=>{try{number(cell);return Boolean(cell);}catch{return false;}});if(start<0)start=0;
  // A bad first numeric value must remain visible as an error; never silently
  // skip it while looking for the start of a labelled Excel row.
  if(/^[\d+−—–=.-]/.test(cells[0]||''))start=0;
  first.value=String(start);last.value=String(Math.min(cells.length-1,start+targets.length-1));
  q('[data-paste-target]').textContent=input.getAttribute('aria-label')+' · '+targets.length+' доступных дней вправо';
  preview();dialog.showModal();
 }
 root.addEventListener('paste',event=>{
  const input=event.target.closest('.smg-day-input');if(!input||input.disabled||input.readOnly)return;
  const text=event.clipboardData?.getData('text/plain');if(text===undefined)return;event.preventDefault();
  try{
   const cells=parseRow(text),row=[...input.closest('tr').querySelectorAll('.smg-day-input:not(:disabled):not([readonly])')],targets=row.slice(row.indexOf(input));
   let values;try{values=validate(targets,cells);}catch{openPreview(input,targets,cells);return;}
   apply(targets,values);
  }catch(e){message('Вставка не выполнена: '+e.message);}
 });
 q('[data-paste-first]').addEventListener('change',()=>{q('[data-paste-last]').value=String(Math.min(pending.cells.length-1,Number(q('[data-paste-first]').value)+pending.targets.length-1));preview();});q('[data-paste-last]').addEventListener('change',preview);
 q('[data-paste-apply]').addEventListener('click',()=>{try{const values=validate(pending.targets,pending.cells.slice(Number(q('[data-paste-first]').value),Number(q('[data-paste-last]').value)+1));dialog.close();apply(pending.targets,values);pending=null;}catch(e){q('[data-paste-error]').hidden=false;q('[data-paste-error]').textContent=e.message;}});
 dialog.querySelectorAll('[data-paste-cancel]').forEach(button=>button.addEventListener('click',()=>{dialog.close();pending?.input.focus({preventScroll:true});pending=null;}));
 dialog.addEventListener('cancel',()=>{pending?.input.focus({preventScroll:true});pending=null;});
 function revert(){
  if(!lastPatch)return false;
  if(lastPatch.some(p=>p.input.value!==p.after)){message('После вставки значения уже менялись. Отменить всю вставку нельзя.');return false;}
  lastPatch.forEach(p=>{p.input.value=p.before;p.input.dispatchEvent(new Event('input',{bubbles:true}));});lastPatch=null;if(undo)undo.hidden=true;message('Вставка отменена.');return true;
 }
 undo?.addEventListener('click',revert);
 root.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&!event.shiftKey&&lastPatch?.some(p=>p.input===event.target)&&lastPatch.every(p=>p.input.value===p.after)){event.preventDefault();revert();}});
})();
