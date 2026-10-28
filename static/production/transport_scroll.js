(() => {
 'use strict';
 const top=document.querySelector('[data-transport-top-scroll]'),viewport=document.querySelector('[data-transport-table-scroll]');
 if(!top||!viewport)return;
 const spacer=top.firstElementChild,table=viewport.querySelector('table');
 function resize(){
  top.hidden=viewport.clientWidth===0||viewport.scrollWidth<=viewport.clientWidth+1;
  spacer.style.width=Math.max(0,viewport.scrollWidth-viewport.clientWidth+top.clientWidth)+'px';
  top.scrollLeft=viewport.scrollLeft;
 }
 function sync(source,target){if(Math.abs(target.scrollLeft-source.scrollLeft)>.5)target.scrollLeft=source.scrollLeft;}
 top.addEventListener('scroll',()=>sync(top,viewport),{passive:true});
 viewport.addEventListener('scroll',()=>sync(viewport,top),{passive:true});
 if('ResizeObserver' in window){const observer=new ResizeObserver(resize);observer.observe(viewport);if(table)observer.observe(table);}
 window.addEventListener('resize',resize);resize();
})();
