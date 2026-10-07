(() => {
  const element = document.getElementById('revenue-data');
  if (!element) return;
  const data = JSON.parse(element.textContent);
  if (!data) return;
  const colors = ['#087cff','#06b5aa','#8247fa','#ff9800','#26bdf5','#96a0b1'];
  const ns='http://www.w3.org/2000/svg';
  const format=value => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value);
  function svg(tag, attrs={}, text) {
    const node=document.createElementNS(ns,tag);
    Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));
    if(text !== undefined)node.textContent=text;
    return node;
  }
  function lineChart(chartData=data, targetId="revenue-line", divisor=1e6, unit="Млн ₽") {
    const target=document.getElementById(targetId);
    if (!target) return;
    const canvas=svg('svg',{viewBox:'0 0 1100 270',role:'img','aria-label':'Накопительная выручка: план и факт'});
    const width=1000,height=210,left=70,top=10;
    const maximum=Math.max(1,...chartData.plan.filter(v=>v!==null),...chartData.fact.filter(v=>v!==null));
    const x=i=>left+i*width/Math.max(1,chartData.labels.length-1), y=v=>top+height-v*height/maximum;
    for(let i=0;i<=4;i++) {
      const value=maximum*i/4;
      canvas.append(svg('line',{x1:left,x2:left+width,y1:y(value),y2:y(value),stroke:'#e2e9f2'}));
      canvas.append(svg('text',{x:left-8,y:y(value)+4,'text-anchor':'end'},format(value/divisor)));
    }
    canvas.append(svg('text',{x:left,y:top+height+43},unit));
    const positions=[...new Set([0,Math.floor((chartData.labels.length-1)/2),chartData.labels.length-1])];
    positions.forEach(i=>canvas.append(svg('text',{x:x(i),y:top+height+22,'text-anchor':'middle'},chartData.labels[i])));
    [['plan',colors[0],'План'],['fact',colors[1],'Факт']].forEach(([key,color,label],seriesIndex)=>{
      let path='',previous=false;
      chartData[key].forEach((value,i)=>{
        if(value===null){previous=false;return;}
        path+=(previous?'L':'M')+x(i)+' '+y(value)+' ';previous=true;
      });
      canvas.append(svg('path',{d:path,fill:'none',stroke:color,'stroke-width':3}));
      chartData[key].forEach((value,i)=>{
        if(value===null)return;
        const dot=svg('circle',{cx:x(i),cy:y(value),r:3,fill:color});
        dot.append(svg('title',{},`${chartData.labels[i]} · ${label}: ${format(value)} ${unit === "Млн ₽" ? "₽" : unit}`));canvas.append(dot);
      });
      canvas.append(svg('text',{x:450+seriesIndex*130,y:265,fill:color},label));
    });
    const last=chartData.fact.map((v,i)=>v!==null?i:-1).filter(i=>i>=0).pop();
    if(last!==undefined){canvas.append(svg('line',{x1:x(last),x2:x(last),y1:top,y2:top+height,stroke:'#8699b0','stroke-dasharray':'4 4'}));}
    target.replaceChildren(canvas);
  }
  function shareChart(targetId, rows, mode, limit) {
    const target=document.getElementById(targetId);target.replaceChildren();
    const all=rows.filter(r=>r[mode]>0).sort((a,b)=>b[mode]-a[mode]);
    const total=all.reduce((sum,r)=>sum+r[mode],0);
    if(!total){target.textContent='Нет выручки для расчёта долей';return;}
    const shown=limit?all.slice(0,limit):all;
    const others=limit?all.slice(limit):[];
    if(others.length)shown.push({name:'Прочие', [mode]:others.reduce((sum,r)=>sum+r[mode],0), others});
    const canvas=svg('svg',{viewBox:'0 0 200 200',role:'img','aria-label':'Доли выручки'});
    const radius=70,circumference=2*Math.PI*radius;
    let offset=0;
    const legend=document.createElement('div');legend.className='share-legend';
    shown.forEach((row,i)=>{
      const share=row[mode]/total,color=colors[i%colors.length];
      const ring=svg('circle',{cx:100,cy:100,r:radius,fill:'none',stroke:color,'stroke-width':38,'stroke-dasharray':`${share*circumference} ${circumference}`,'stroke-dashoffset':-offset*circumference,transform:'rotate(-90 100 100)'});
      ring.append(svg('title',{},`${row.name}: ${format(row[mode])} ₽ (${format(share*100)}%)`));
      if(row.url){const link=svg('a',{href:row.url});link.append(ring);canvas.append(link);}else canvas.append(ring);
      offset+=share;
      const entry=document.createElement(row.others?'details':'a');
      const label=document.createElement(row.others?'summary':'span');
      const dot=document.createElement('span');dot.className='share-color';dot.style.backgroundColor=color;label.append(dot);
      label.append(document.createTextNode(`${row.name} — ${format(share*100)}%`));entry.append(label);
      if(row.url)entry.href=row.url;
      if(row.others)row.others.forEach(other=>{const a=document.createElement('a');a.href=other.url;a.style.display='block';a.textContent=`${other.name} — ${format(other[mode]/total*100)}%`;entry.append(a);});
      legend.append(entry);
    });
    canvas.append(svg('text',{x:100,y:96,'text-anchor':'middle','font-size':20,'font-weight':700},format(total/1e6)));
    canvas.append(svg('text',{x:100,y:117,'text-anchor':'middle','font-size':12},'млн ₽'));
    target.append(canvas,legend);
  }
  function shares(mode){shareChart('object-shares',data.objects,mode);shareChart('work-shares',data.works,mode,5);}
  document.querySelectorAll('[data-share-mode]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('[data-share-mode]').forEach(b=>{b.classList.toggle('btn-primary',b===button);b.classList.toggle('btn-outline-primary',b!==button);b.setAttribute('aria-pressed',String(b===button));});
    shares(button.dataset.shareMode);
  }));
  document.querySelectorAll('[data-object-toggle]').forEach(button=>button.addEventListener('click',()=>{
    const open=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(open));button.textContent=open?'▾':'▸';
    document.querySelectorAll('[data-object-child]').forEach(row=>{if(row.dataset.objectChild===button.dataset.objectToggle)row.hidden=!open;});
  }));
  lineChart();
  if(data.detail && data.detail.labels.length)lineChart(data.detail,'work-trend',1,'Объём');
  shares('fact');
})();
