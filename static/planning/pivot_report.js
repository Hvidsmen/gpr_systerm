(() => {
 'use strict';
 const $=id=>document.getElementById(id), read=id=>JSON.parse($(id).textContent);
 let config=read('pivot-initial'); const fields=read('pivot-fields'),base=read('pivot-base-measures');
 const defaults={rows:['building','complex_object','package','work'],columns:['month'],measures:['plan','fact','delta'],formulas:[]};
 const button=(text,fn,label)=>{const b=document.createElement('button');b.type='button';b.textContent=text;if(label)b.setAttribute('aria-label',label);b.addEventListener('click',fn);return b;};
 function render(){
  for(const axis of ['rows','columns']){
   const ol=$('pivot-'+axis);ol.replaceChildren();
   config[axis].forEach((key,index)=>{const li=document.createElement('li'),span=document.createElement('span');span.textContent=fields[key];li.append(span);
    li.append(button('↑',()=>{if(index){[config[axis][index-1],config[axis][index]]=[key,config[axis][index-1]];render();}},'Выше: '+fields[key]));
    li.append(button('↓',()=>{if(index<config[axis].length-1){[config[axis][index+1],config[axis][index]]=[key,config[axis][index+1]];render();}},'Ниже: '+fields[key]));
    li.append(button('⇄',()=>{config[axis].splice(index,1);config[axis==='rows'?'columns':'rows'].push(key);render();},'Перенести на другую ось: '+fields[key]));
    li.append(button('×',()=>{config[axis].splice(index,1);render();},'Убрать: '+fields[key]));ol.append(li);
   });
   const select=$(axis==='rows'?'pivot-row-add':'pivot-column-add');select.replaceChildren();
   Object.entries(fields).filter(([key])=>![...config.rows,...config.columns].includes(key)).forEach(([key,label])=>{const o=document.createElement('option');o.value=key;o.textContent=label;select.append(o);});
  }
  const target=$('pivot-measures');target.replaceChildren();
  const measures=[...Object.entries(base),...config.formulas.map(f=>[f.id,f.name])];
  measures.forEach(([id,label])=>{const line=document.createElement('div'),l=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=config.measures.includes(id);input.addEventListener('change',()=>{config.measures=input.checked?[...config.measures,id]:config.measures.filter(v=>v!==id);});l.append(input,document.createTextNode(' '+label));line.append(l);
   if(id.startsWith('custom_'))line.append(button('×',()=>{const remaining=config.formulas.filter(f=>f.id!==id),chosen=new Set(config.measures);config.formulas=remaining.map((f,i)=>({...f,id:'custom_'+i}));config.measures=config.measures.filter(v=>!v.startsWith('custom_'));remaining.forEach((f,i)=>{if(chosen.has(f.id))config.measures.push('custom_'+i);});render();},'Удалить формулу '+label));target.append(line);
  });
 }
 document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>{const axis=b.dataset.add,key=$(axis==='rows'?'pivot-row-add':'pivot-column-add').value;if(key){config[axis].push(key);render();}}));
 $('formula-add').addEventListener('click',()=>{const name=$('formula-name').value.trim(),expression=$('formula-expression').value.trim();if(!name||!expression){alert('Укажите название и формулу.');return;}if(config.formulas.length>=10){alert('Можно добавить до 10 формул.');return;}const id='custom_'+config.formulas.length;config.formulas.push({id,name,expression,percent:$('formula-percent').checked});config.measures.push(id);$('formula-name').value=$('formula-expression').value='';render();});
 $('pivot-form').addEventListener('submit',()=>{$('pivot-config').value=JSON.stringify(config);});
 $('pivot-save').addEventListener('submit',()=>{$('pivot-save-config').value=JSON.stringify(config);});
 $('pivot-update').addEventListener('click',()=>{if(!$('pivot-preset').value){alert('Выберите вариант для обновления.');return;}$('pivot-save-preset').value=$('pivot-preset').value;$('pivot-save').requestSubmit();});
 $('pivot-load').addEventListener('click',()=>{const u=new URL(location.href);u.searchParams.delete('config');u.searchParams.delete('export');if($('pivot-preset').value)u.searchParams.set('preset',$('pivot-preset').value);else u.searchParams.delete('preset');location.assign(u);});
 for(const action of ['default','delete'])$('pivot-'+action).addEventListener('click',()=>{const id=$('pivot-preset').value;if(!id){alert('Выберите сохранённый вариант.');return;}if(action==='delete'&&!confirm('Удалить этот личный вариант?'))return;const form=$('pivot-save');form.elements.namedItem('action').value=action;$('pivot-save-preset').value=id;form.requestSubmit();});
 $('pivot-reset').addEventListener('click',()=>{config=structuredClone(defaults);render();});render();
})();
