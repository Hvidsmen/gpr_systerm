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
    const legend=document.createElement('div');legend.className='chart-series-legend chart-series-legend-top';
    [['План — линия','#087cff','chart-series-line'],['Факт — столбцы','#18b7a5','chart-series-bar']].forEach(([label,color,shape])=>{
      const item=document.createElement('span'),mark=document.createElement('i');mark.className=shape;mark.style.backgroundColor=color;
      item.append(mark,document.createTextNode(label));legend.append(item);
    });
    target.replaceChildren(chart,legend);
  }
  document.querySelectorAll('.resource-section').forEach(section=>{
    const target=section.querySelector('[data-resource-trend]');
    if(!target)return;
    const overall=target.dataset.resourceTrend;
    const initialLabel=section.querySelector('[data-resource-caption]').textContent;
    const totalFields=[...section.querySelectorAll('[data-resource-total]')];
    const totalLabel=section.querySelector('[data-resource-total-label]');
    const originalTotals=totalFields.map(field=>({field,text:field.textContent,negative:field.classList.contains('text-danger')}));
    const originalTotalLabel=totalLabel?.textContent;
    function updateTotals(selected){
      if(!totalFields.length)return;
      if(!selected){
        originalTotals.forEach(({field,text,negative})=>{field.textContent=text;field.classList.toggle('text-danger',negative);});
        if(totalLabel)totalLabel.textContent=originalTotalLabel;
        return;
      }
      const trend=JSON.parse(selected.dataset.resourceSelect);
      const index=trend.labels.indexOf(section.dataset.resourceCutoff);
      const plan=index>=0?trend.plan[index]:null,fact=index>=0?trend.fact[index]:null;
      const values={plan,fact,delta:plan!==null&&fact!==null?fact-plan:null};
      const number=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:section.dataset.resourceKind==='fuel'?1:0});
      totalFields.forEach(field=>{const value=values[field.dataset.resourceTotal];field.textContent=value===null?'—':number.format(value);field.classList.toggle('text-danger',field.dataset.resourceTotal==='delta'&&value!==null&&value<0);});
      if(totalLabel)totalLabel.textContent=selected.dataset.resourceLabel;
    }
    let current=null;
    function select(selected){
      current=selected;
      updateTotals(selected);
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
    const shareTarget=section.querySelector('[data-resource-shares]');
    function shares(mode){
      if(!shareTarget)return;
      shareTarget.replaceChildren();
      const colors=['#087cff','#18b7a5','#8247fa','#ff9800','#26bdf5','#e76588','#718399'];
      const rows=[...section.querySelectorAll('.resource-site > summary')].map(summary=>({
        summary,name:summary.dataset.resourceLabel,
        value:Number(mode==='plan'?summary.dataset.resourcePlan:summary.dataset.resourceFact)
      })).filter(row=>Number.isFinite(row.value)&&row.value>0).sort((a,b)=>b.value-a.value);
      const total=rows.reduce((sum,row)=>sum+row.value,0);
      if(!total){shareTarget.textContent='Нет данных для диаграммы';return;}
      const chart=node('svg',{viewBox:'0 0 220 220',role:'img','aria-label':'Доли ресурсов по объектам: '+(mode==='plan'?'план':'факт')});
      const circumference=2*Math.PI*76;let offset=0;
      const legend=document.createElement('div');legend.className='resource-share-legend';
      rows.forEach((row,index)=>{
        const share=row.value/total,color=colors[index%colors.length];
        const choose=()=>{row.summary.parentElement.open=true;select(row.summary);};
        const segment=node('circle',{cx:110,cy:110,r:76,fill:'none',stroke:color,'stroke-width':32,'stroke-dasharray':`${share*circumference} ${circumference}`,'stroke-dashoffset':-offset*circumference,transform:'rotate(-90 110 110)',tabindex:0,role:'button','aria-label':row.name});
        segment.append(node('title',{},`${row.name}: ${format(row.value)} (${format(share*100)}%)`));
        segment.addEventListener('click',choose);segment.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});chart.append(segment);offset+=share;
        const button=document.createElement('button');button.type='button';button.className='resource-share-item';
        const dot=document.createElement('span');dot.className='share-color';dot.style.backgroundColor=color;
        button.append(dot,document.createTextNode(`${row.name} — ${format(row.value)} · ${format(share*100)}%`));button.addEventListener('click',choose);legend.append(button);
      });
      chart.append(node('text',{x:110,y:110,'text-anchor':'middle','font-size':22,'font-weight':700},format(total)),node('text',{x:110,y:132,'text-anchor':'middle','font-size':12},mode==='plan'?'План':'Факт'));
      shareTarget.append(chart,legend);
    }
    section.querySelectorAll('[data-resource-share-mode]').forEach(button=>button.addEventListener('click',()=>{
      section.querySelectorAll('[data-resource-share-mode]').forEach(item=>{
        item.classList.toggle('btn-primary',item===button);item.classList.toggle('btn-outline-primary',item!==button);item.setAttribute('aria-pressed',String(item===button));
      });shares(button.dataset.resourceShareMode);
    }));
    shares('plan');
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
