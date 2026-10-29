const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.TRANSPORT_SCROLL_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url+`?camp=${c.camp}&date=${c.day}`);
  const top=page.locator('[data-transport-top-scroll]');assert(await top.isVisible());
  const offset=await top.evaluate(el=>Math.min(600,el.scrollWidth-el.clientWidth));await top.evaluate((el,value)=>el.scrollLeft=value,offset);
  await page.waitForFunction(value=>Math.abs(document.querySelector('[data-transport-table-scroll]').scrollLeft-value)<2,offset);
  const back=Math.min(320,offset);await page.locator('[data-transport-table-scroll]').evaluate((el,value)=>el.scrollLeft=value,back);
  await page.waitForFunction(value=>Math.abs(document.querySelector('[data-transport-top-scroll]').scrollLeft-value)<2,back);
  await page.screenshot({path:'/tmp/transport-top-scroll-wide.png',fullPage:true});
  for(const width of [1100,390,1530]){
   await page.setViewportSize({width,height:1040});
   await page.waitForFunction(()=>{const a=document.querySelector('[data-transport-top-scroll]'),b=document.querySelector('[data-transport-table-scroll]');return a.hidden||Math.abs(a.scrollLeft-b.scrollLeft)<2;});
   await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+2,{},{timeout:3000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),JSON.stringify(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,containers:[...document.querySelectorAll("[data-transport-top-scroll],[data-transport-table-scroll],[data-transport-board],main")].map(el=>({cls:el.className,width:el.clientWidth,overflow:getComputedStyle(el).overflow,rect:el.getBoundingClientRect().toJSON()})),wide:[...document.querySelectorAll("body *")].filter(el=>el.getBoundingClientRect().right>innerWidth+2&&getComputedStyle(el).position!=="fixed").slice(0,12).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))}))));
  }
  assert(await top.isVisible());await top.evaluate(el=>el.scrollLeft=el.scrollWidth);
  await page.waitForFunction(()=>Math.abs(document.querySelector('[data-transport-top-scroll]').scrollLeft-document.querySelector('[data-transport-table-scroll]').scrollLeft)<2);
  assert.deepEqual(errors,[]);console.log('PASS: upper equipment scrollbar, two-way synchronization and responsive resizing');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
