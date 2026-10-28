/* The header and every section share one horizontal coordinate system. */
(()=>{
 'use strict';
 const grid=document.querySelector('[data-smg-calendar-grid]');if(!grid)return;
 const header=grid.querySelector('[data-smg-calendar-header]'),headScroll=grid.querySelector('[data-smg-calendar-header-scroll]'),body=grid.querySelector('[data-smg-calendar-scroll]'),matrix=grid.querySelector('[data-plan-matrix]');
 const action=document.querySelector('.smg-editor .plan-action-header'),add=document.querySelector('.smg-editor .plan-workbench-add');
 grid.style.setProperty('--smg-numeric-columns',String(headScroll.querySelectorAll('th').length-2));
 function sync(source,target){if(Math.abs(source.scrollLeft-target.scrollLeft)>.5)target.scrollLeft=source.scrollLeft;}
 headScroll.addEventListener('scroll',()=>sync(headScroll,body),{passive:true});body.addEventListener('scroll',()=>sync(body,headScroll),{passive:true});
 let scheduled=false;
 function layout(){
  scheduled=false;
  let top=action?(parseFloat(getComputedStyle(action).top)||0)+action.getBoundingClientRect().height+6:60;
  if(add)top+=add.getBoundingClientRect().height+6;
  grid.style.setProperty('--smg-calendar-top',top+'px');
  const origin=matrix.getBoundingClientRect().left;
  const rows=[...matrix.querySelectorAll('.matrix-scroll,.smg-calendar-summary')].filter(row=>row.getClientRects().length);
  const insets=rows.map(row=>Math.max(0,row.getBoundingClientRect().left-origin+(parseFloat(row.style.getPropertyValue('--smg-inset'))||0)));
  rows.forEach((row,index)=>row.style.setProperty('--smg-inset',insets[index]+'px'));
  sync(body,headScroll);
 }
 function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(layout);}}
 window.addEventListener('resize',schedule);matrix.addEventListener('toggle',schedule,true);
 new MutationObserver(schedule).observe(matrix,{subtree:true,attributes:true,attributeFilter:['hidden','open']});
 if(window.ResizeObserver){const observer=new ResizeObserver(schedule);[action,add,grid].filter(Boolean).forEach(node=>observer.observe(node));}
 layout();
})();
