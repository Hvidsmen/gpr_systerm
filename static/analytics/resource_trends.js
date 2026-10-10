(() => {
  const ns='http://www.w3.org/2000/svg';
  const format=v=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(v);
  const node=(tag,attrs={},text)=>{const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;return n;};
  function draw(target){
    if(target.dataset.rendered)return;
    target.classList.remove('resource-trend-empty');
    const data=JSON.parse(target.dataset.resourceTrend);
    target.dataset.rendered='1';
    if(!data.labels.length||!data.plan.some(v=>v!==null)&&!data.fact.some(v=>v!==null)){target.textContent='Нет данных за выбранный период';target.classList.add('resource-trend-empty');return;}
    const small=target.classList.contains('resource-trend-small');
    const w=1000,h=small?130:260,left=48,right=15,top=22,bottom=32;
    const chart=node('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':target.getAttribute('aria-label')+'; план линия, факт столбцы'});
    const maximum=Math.max(1,...data.plan.filter(v=>v!==null),...data.fact.filter(v=>v!==null));
    const step=(w-left-right)/data.labels.length;
    const x=i=>left+(i+.5)*step,y=v=>h-bottom-v*(h-top-bottom)/maximum;
    [0,.5,1].forEach(part=>{const v=maximum*part;chart.append(node('line',{x1:left,x2:w-right,y1:y(v),y2:y(v),stroke:'#e4ebf4'}),node('text',{x:left-5,y:y(v)+4,'text-anchor':'end'},format(v)));});
    data.fact.forEach((value,i)=>{if(value===null)return;const bar=node('rect',{x:x(i)-step*.34,y:y(value),width:Math.max(.5,step*.68),height:Math.max(value===0?1:0,h-bottom-y(value)),fill:'#18b7a5',opacity:.75});bar.append(node('title',{},`${data.labels[i]} · Факт: ${format(value)}`));chart.append(bar);});
    let path='',connected=false;
    data.plan.forEach((value,i)=>{if(value===null){connected=false;return;}path+=(connected?'L':'M')+x(i)+' '+y(value)+' ';connected=true;});
    chart.append(node('path',{d:path,fill:'none',stroke:'#087cff','stroke-width':2.5}));
    data.plan.forEach((value,i)=>{if(value===null)return;const point=node('circle',{cx:x(i),cy:y(value),r:data.labels.length>90?1.5:3,fill:'#087cff'});point.append(node('title',{},`${data.labels[i]} · План: ${format(value)}`));chart.append(point);});
    [...new Set([0,Math.floor((data.labels.length-1)/2),data.labels.length-1])].forEach(i=>chart.append(node('text',{x:x(i),y:h-8,'text-anchor':i===0?'start':i===data.labels.length-1?'end':'middle'},data.labels[i])));
    chart.append(node('text',{x:left,y:13,fill:'#087cff'},'План — линия'),node('text',{x:left+130,y:13,fill:'#18b7a5'},'Факт — столбцы'));
    target.replaceChildren(chart);
  }
  document.querySelectorAll('.resource-section').forEach(section=>{
    const target=section.querySelector('[data-resource-trend]');
    if(!target)return;
    const overall=target.dataset.resourceTrend;
    const initialLabel=section.querySelector('[data-resource-caption]').textContent;
    let current=null;
    function select(selected){
      current=selected;
      section.querySelectorAll('[data-resource-select]').forEach(item=>{
        item.classList.toggle('resource-selected',item===selected);
        if(item===selected)item.setAttribute('aria-current','true');else item.removeAttribute('aria-current');
      });
      const label=selected?selected.dataset.resourceLabel:initialLabel;
      section.querySelector('[data-resource-caption]').textContent=label;
      target.dataset.resourceTrend=selected?selected.dataset.resourceSelect:overall;
      target.setAttribute('aria-label','Динамика: '+label);
      delete target.dataset.rendered;
      draw(target);
    }
    section.addEventListener('click',event=>{
      if(event.target.closest('[data-resource-reset]')){select(null);return;}
      const selected=event.target.closest('[data-resource-select]');
      if(selected && selected.tagName!=='SUMMARY')select(selected);
    });
    section.addEventListener('toggle',event=>{
      const details=event.target;
      if(details.tagName!=='DETAILS')return;
      const summary=details.querySelector(':scope > summary[data-resource-select]');
      if(!summary)return;
      if(details.open){select(summary);return;}
      if(!current || !details.contains(current))return;
      let parent=details.parentElement.closest('details');
      while(parent && section.contains(parent)){
        if(parent.open){
          const parentSummary=parent.querySelector(':scope > summary[data-resource-select]');
          if(parentSummary){select(parentSummary);return;}
        }
        parent=parent.parentElement.closest('details');
      }
      select(null);
    },true);
  });
  const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){draw(entry.target);observer.unobserve(entry.target);}}),{rootMargin:'100px'});
  document.querySelectorAll('[data-resource-trend]').forEach(target=>observer.observe(target));
})();
