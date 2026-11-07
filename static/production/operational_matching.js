(()=>{
 const form=document.querySelector('[data-operational-match-form]');if(!form)return;
 const target=form.querySelector('#match-target'),filters={
  complexObject:form.querySelector('[data-match-filter="complexObject"]'),
  complex:form.querySelector('[data-match-filter="complex"]'),
  member:form.querySelector('[data-match-filter="member"]'),
 };
 const options=[...target.options].filter(option=>option.value);
 const matches=(option,key)=>!filters[key].value||option.dataset[key]===filters[key].value;
 function fill(key,eligible){
  const select=filters[key],current=select.value;
  const values=[...new Set(eligible.map(option=>option.dataset[key]).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
  select.replaceChildren(new Option('Все',''),...values.map(value=>new Option(value,value)));
  if(values.includes(current))select.value=current;
 }
 function update(changed){
  if(changed==='complexObject'){filters.complex.value='';filters.member.value='';}
  if(changed==='complex')filters.member.value='';
  fill('complexObject',options);
  const byObject=options.filter(option=>matches(option,'complexObject'));
  fill('complex',byObject);
  const byComplex=byObject.filter(option=>matches(option,'complex'));
  fill('member',byComplex);
  const visible=options.filter(option=>matches(option,'complexObject')&&matches(option,'complex')&&matches(option,'member'));
  const allowed=new Set(visible);
  options.forEach(option=>{option.hidden=!allowed.has(option);option.disabled=!allowed.has(option);});
  if(target.selectedOptions[0]?.disabled)target.value='';
 }
 Object.entries(filters).forEach(([key,select])=>select.addEventListener('change',()=>update(key)));
 update();
})();
