(() => {
 function create(container,label,options,onchange){
  const root=document.createElement('details');root.className='rg-multi';
  const summary=document.createElement('summary');summary.textContent=label+' · Все';root.append(summary);
  const panel=document.createElement('div');panel.className='rg-multi-panel';
  const search=document.createElement('input');search.type='search';search.placeholder='Найти';search.setAttribute('aria-label','Найти: '+label);panel.append(search);
  const tools=document.createElement('div');tools.className='rg-multi-tools';
  const all=document.createElement('button'),clear=document.createElement('button');all.type=clear.type='button';all.textContent='Выбрать найденные';clear.textContent='Сбросить';tools.append(all,clear);panel.append(tools);
  const list=document.createElement('div');list.className='rg-multi-options';list.setAttribute('role','group');list.setAttribute('aria-label',label);panel.append(list);root.append(panel);container.append(root);
  let selected=new Set(),items=[];
  const update=()=>{const chosen=items.filter(i=>selected.has(String(i.value)));summary.textContent=label+' · '+(chosen.length===1?chosen[0].label:chosen.length?'Выбрано '+chosen.length:'Все');summary.title=chosen.map(i=>i.label).join(', ');};
  const filter=()=>{const query=search.value.toLocaleLowerCase('ru').trim();for(const row of list.children)row.hidden=!row.textContent.toLocaleLowerCase('ru').includes(query);};
  function setOptions(values){items=values;const allowed=new Set(items.map(i=>String(i.value)));selected=new Set([...selected].filter(v=>allowed.has(v)));list.replaceChildren();for(const item of items){const row=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=String(item.value);box.checked=selected.has(box.value);row.append(box,document.createTextNode(item.label));list.append(row);box.addEventListener('change',()=>{if(box.checked)selected.add(box.value);else selected.delete(box.value);update();onchange();});}update();filter();}
  function set(values){selected=new Set(values.map(String));setOptions(items);}
  all.addEventListener('click',()=>{for(const row of list.children)if(!row.hidden){const box=row.querySelector('input');box.checked=true;selected.add(box.value);}update();onchange();});clear.addEventListener('click',()=>{set([]);onchange();});
  search.addEventListener('input',filter);search.addEventListener('keydown',e=>{if(e.key==='Enter')e.preventDefault();});
  root.addEventListener('toggle',()=>{if(root.open){container.querySelectorAll('.rg-multi[open]').forEach(other=>{if(other!==root)other.open=false;});search.focus();}});
  document.addEventListener('click',e=>{if(!root.contains(e.target))root.open=false;});
  setOptions(options);return {root,values:()=>[...selected],set,setOptions};
 }
 window.graphMultiselect={create};
})();
