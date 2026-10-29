(()=>{
 const grid=document.querySelector('[data-roster-grid]');if(!grid)return;
 const head=grid.querySelector('[data-roster-head-scroll]'),body=grid.querySelector('[data-roster-body]'),heading=grid.querySelector('[data-roster-heading]'),filter=document.querySelector('[data-rotation-filter]');
 function sync(a,b){if(Math.abs(a.scrollLeft-b.scrollLeft)>.5)b.scrollLeft=a.scrollLeft;}
 head.addEventListener('scroll',()=>sync(head,body),{passive:true});body.addEventListener('scroll',()=>sync(body,head),{passive:true});
 let queued=false;function layout(){queued=false;if(!grid.clientWidth)return;grid.style.setProperty('--roster-width',Math.max(1495,grid.clientWidth-2)+'px');grid.style.setProperty('--roster-viewport',grid.clientWidth+'px');let top=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--navbar-height'))||48;if(filter)top=(parseFloat(getComputedStyle(filter).top)||top)+filter.getBoundingClientRect().height+6;heading.style.setProperty('--roster-top',top+'px');sync(body,head);}
 function schedule(){if(!queued){queued=true;requestAnimationFrame(layout);}}window.addEventListener('resize',schedule);if(window.ResizeObserver){const o=new ResizeObserver(schedule);o.observe(grid);if(filter)o.observe(filter);}layout();
})();
