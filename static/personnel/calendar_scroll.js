(()=>{
 const top=document.querySelector('[data-calendar-top-scroll]'),viewport=document.querySelector('[data-calendar-table-scroll]');if(!top||!viewport)return;
 const table=viewport.querySelector('table');let shown=false,lastWidth=0;
 // Padding columns let dates near either end of the month reach the centre.
 for(const row of table.rows){if(row.children.length<2||row.classList.contains('calendar-day-row'))continue;for(const side of ['start','end']){const spacer=document.createElement(row.parentElement.tagName==='THEAD'?'th':'td');spacer.className='housing-calendar-buffer';if(row.classList.contains('calendar-month-row'))spacer.rowSpan=2;spacer.setAttribute('aria-hidden','true');if(side==='start')row.insertBefore(spacer,row.children[1]);else row.append(spacer);}}
 function resize(){
  if(!viewport.clientWidth){top.hidden=true;shown=false;return;}
  const sticky=table.rows[0]?.cells[0]?.getBoundingClientRect().width||0;
  viewport.style.setProperty('--calendar-buffer',Math.max(0,(viewport.clientWidth-sticky)/2)+'px');
  top.hidden=viewport.scrollWidth<=viewport.clientWidth+1;top.firstElementChild.style.width=viewport.scrollWidth+'px';
  if(!shown||lastWidth!==viewport.clientWidth){const selected=table.querySelector('[data-calendar-selected]');if(selected){const a=selected.getBoundingClientRect(),b=viewport.getBoundingClientRect();viewport.scrollLeft=a.left-b.left+viewport.scrollLeft+a.width/2-sticky-(viewport.clientWidth-sticky)/2;}}
  shown=true;lastWidth=viewport.clientWidth;top.scrollLeft=viewport.scrollLeft;
 }
 top.addEventListener('scroll',()=>{if(Math.abs(viewport.scrollLeft-top.scrollLeft)>.5)viewport.scrollLeft=top.scrollLeft;},{passive:true});viewport.addEventListener('scroll',()=>{if(Math.abs(top.scrollLeft-viewport.scrollLeft)>.5)top.scrollLeft=viewport.scrollLeft;},{passive:true});
 const observer=new ResizeObserver(resize);observer.observe(viewport);observer.observe(table);window.addEventListener('resize',resize);resize();
})();
