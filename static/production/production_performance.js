(()=>{
 'use strict';
 const form=document.querySelector('[data-production-form]');if(!form)return;
 const started=performance.now(),metrics={navigation:{},events:{},longTasks:[]};
 const navigation=performance.getEntriesByType('navigation')[0];
 if(navigation)metrics.navigation={response:Math.round(navigation.responseEnd-navigation.requestStart),dom:Math.round(navigation.domContentLoadedEventEnd-navigation.startTime),bytes:navigation.transferSize||0};
 function mark(name){metrics.events[name]=Math.round(performance.now()-started);}
 mark('scripts_ready');
 requestAnimationFrame(()=>requestAnimationFrame(()=>mark('works_painted')));
 const idle=window.requestIdleCallback||((callback)=>setTimeout(()=>callback({didTimeout:false,timeRemaining:()=>0}),1));
 idle(()=>{mark('background_idle');form.dataset.backgroundReady='1';form.dispatchEvent(new Event('production-background-ready'));},{timeout:1500});
 if('PerformanceObserver' in window){try{new PerformanceObserver(list=>{for(const entry of list.getEntries())metrics.longTasks.push({start:Math.round(entry.startTime),duration:Math.round(entry.duration)});if(metrics.longTasks.length>30)metrics.longTasks.splice(0,metrics.longTasks.length-30);}).observe({type:'longtask',buffered:true});}catch{}}
 form.addEventListener('production-step-change',event=>{metrics.events['step_'+event.detail]=Math.round(performance.now()-started);});
 window.productionPerformance={metrics,snapshot:()=>structuredClone(metrics)};
})();
