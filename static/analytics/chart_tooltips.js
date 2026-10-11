(() => {
  const tip=document.createElement('div');tip.className='gpr-chart-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.append(tip);
  function hide(){tip.hidden=true;}
  function show(text,event){
    if(!text){hide();return;}
    tip.textContent=text;tip.hidden=false;
    const box=tip.getBoundingClientRect();
    tip.style.left=Math.max(8,Math.min(event.clientX+14,innerWidth-box.width-8))+'px';
    tip.style.top=Math.max(8,Math.min(event.clientY+14,innerHeight-box.height-8))+'px';
  }
  window.GprChartTooltip={attach(chart,content){
    chart.addEventListener('pointermove',event=>{
      const matrix=chart.getScreenCTM();if(!matrix)return;
      const point=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
      show(content(point),event);
    });
    chart.addEventListener('pointerleave',hide);
  },hide};
  document.addEventListener('pointermove',event=>{
    const item=event.target.closest?.('[data-chart-tooltip]');
    if(item)show(item.dataset.chartTooltip,event);
    else if(!event.target.closest?.('.revenue-chart,.resource-trend'))hide();
  });
  document.addEventListener('focusin',event=>{
    const item=event.target.closest?.('[data-chart-tooltip]');if(!item)return;
    const box=item.getBoundingClientRect();show(item.dataset.chartTooltip,{clientX:box.left,clientY:box.bottom});
  });
  document.addEventListener('focusout',hide);
  document.addEventListener('click',hide);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
  window.addEventListener('scroll',hide,true);
})();
