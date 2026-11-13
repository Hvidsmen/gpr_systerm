(()=>{
 'use strict';
 const input=document.querySelector('[data-work-search]');
 if(!input)return;
 const current=new URL(location.href).searchParams.get('work_q')||'';
 input.value=current;
 const actions=document.createElement('div');actions.className='d-flex gap-2 mt-2';
 const submit=document.createElement('button');submit.type='button';submit.className='btn btn-sm btn-primary';submit.textContent='Найти во всех работах';
 const clear=document.createElement('button');clear.type='button';clear.className='btn btn-sm btn-outline-secondary';clear.textContent='Сбросить';clear.hidden=!current;
 function open(value){
  const url=new URL(location.href);
  if(value)url.searchParams.set('work_q',value);else url.searchParams.delete('work_q');
  url.searchParams.set('work_page','1');url.searchParams.set('entry_step','1');location.assign(url);
 }
 submit.addEventListener('click',()=>open(input.value.trim()));
 clear.addEventListener('click',()=>open(''));
 input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();open(input.value.trim());}});
 actions.append(submit,clear);
 input.parentElement.insertBefore(actions,input.parentElement.querySelector('[data-production-filters="works"]'));
})();
