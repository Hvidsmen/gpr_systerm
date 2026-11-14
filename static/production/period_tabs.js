/* Accessible sections for the standalone page and the lazy production report. */
(()=>{
 const scrolls=new Map();
 function sizeScrolls(){
  scrolls.forEach((state,grid)=>{
   if(!grid.isConnected){state.observer.disconnect();scrolls.delete(grid);return;}
   if(!grid.clientWidth)return;
   state.top.hidden=grid.scrollWidth<=grid.clientWidth+1;
   state.top.style.width=grid.clientWidth+'px';
   state.top.firstElementChild.style.width=grid.scrollWidth+'px';
   if(!state.positioned){const current=grid.querySelector('[data-report-current-day]');if(current)grid.scrollLeft=Math.max(0,current.offsetLeft+current.offsetWidth-grid.clientWidth);state.positioned=true;}
   state.top.scrollLeft=grid.scrollLeft;
  });
 }
 function initScrolls(root){
  root.querySelectorAll('[data-report-day-grid]').forEach(grid=>{
   if(scrolls.has(grid))return;
   const top=grid.parentElement.querySelector('[data-report-top-scroll]');if(!top)return;
   top.addEventListener('scroll',()=>{if(grid.scrollLeft!==top.scrollLeft)grid.scrollLeft=top.scrollLeft;},{passive:true});
   grid.addEventListener('scroll',()=>{if(top.scrollLeft!==grid.scrollLeft)top.scrollLeft=grid.scrollLeft;},{passive:true});
   const observer=new ResizeObserver(sizeScrolls);observer.observe(grid);observer.observe(grid.querySelector('table'));
   scrolls.set(grid,{top,observer,positioned:false});
  });
  sizeScrolls();
 }
 function activate(tabs,key,{focus=false,remember=false}={}){
  const buttons=[...tabs.querySelectorAll('[data-period-tab]')];
  const active=buttons.find(button=>button.dataset.periodTab===key)||buttons[0];if(!active)return;
  const selected=active.dataset.periodTab;
  buttons.forEach(button=>{const chosen=button===active;button.setAttribute('aria-selected',String(chosen));button.tabIndex=chosen?0:-1;});
  tabs.querySelectorAll('[data-period-panel]').forEach(panel=>{panel.hidden=panel.dataset.periodPanel!==selected;});
  const report=tabs.closest('[data-period-content], [data-equipment-report]')||tabs.parentElement;
  const days=report.querySelector('[data-period-days-toggle]');if(days)days.hidden=selected==='fuel';
  if(remember){const url=new URL(location.href);url.searchParams.set('report_section',selected);history.replaceState(history.state,'',url);}
  sizeScrolls();
  if(focus)active.focus();
 }
 function init(root=document){
  initScrolls(root);
  const key=new URL(location.href).searchParams.get('report_section');
  root.querySelectorAll('[data-period-tabs]').forEach(tabs=>activate(tabs,key));
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-period-tab]');if(!button)return;
  activate(button.closest('[data-period-tabs]'),button.dataset.periodTab,{remember:true});
 });
 document.addEventListener('keydown',event=>{
  const button=event.target.closest('[data-period-tab]');if(!button)return;
  const tabs=button.closest('[data-period-tabs]'),buttons=[...tabs.querySelectorAll('[data-period-tab]')];let index=buttons.indexOf(button);
  if(event.key==='ArrowRight')index=(index+1)%buttons.length;
  else if(event.key==='ArrowLeft')index=(index-1+buttons.length)%buttons.length;
  else if(event.key==='Home')index=0;
  else if(event.key==='End')index=buttons.length-1;
  else return;
  event.preventDefault();activate(tabs,buttons[index].dataset.periodTab,{focus:true,remember:true});
 });
 window.productionPeriodTabs={init};init();
 window.addEventListener('popstate',()=>init());
})();
