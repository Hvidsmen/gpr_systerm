const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.PIVOT_INLINE_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1720,height:1050}});
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   if(url.endsWith('/bootstrap-icons.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap-icons.css')});return route.abort();});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const config={rows:['building','package','work'],columns:['month'],measures:['real_plan'],formulas:[],view:'hierarchy',
   totals:{row_levels:['building','package'],column_levels:[],row_grand:true,column_grand:true,position:'after'}};
  const load=()=>page.goto(c.base+c.url+'?'+new URLSearchParams({start:'2026-01-01',end:'2026-01-31',config:JSON.stringify(config)}),{waitUntil:'networkidle'});
  await load();const rows=()=>page.locator('#pivot-result tbody tr'),first=()=>rows().first();
  assert.equal(await page.locator('#pivot-result .pivot-result-subtotal').count(),0);
  assert((await first().locator('td').last().innerText()).replace(/\s/g,'').includes('1050'));
  const summary=await first().innerText();
  await first().locator('[data-pivot-node]').click();
  assert.equal(await rows().filter({visible:true}).count(),2,'Root summary and grand total remain visible');
  assert.equal((await first().innerText()).replace('▸',''),summary.replace('▾',''));
  await first().locator('[data-pivot-node]').click();
  assert(await rows().filter({visible:true}).count()>2);
  await page.locator('#pivot-collapse-all').click();assert.equal(await rows().filter({visible:true}).count(),2);
  await page.locator('#pivot-expand-all').click();assert.equal(await rows().filter({visible:true}).count(),await rows().count());
  await page.locator('#pivot-result').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/pivot-inline-wide.png'});
  config.measures=['plan','fact','completion'];await load();
  const rootRows=page.locator('#pivot-result tbody tr[data-pivot-ancestors=""]');
  const totals=await rootRows.allTextContents();
  assert.equal(await rootRows.locator('[data-pivot-node]').count(),1,'One disclosure control for all measures and units');
  await page.locator('#pivot-collapse-all').click();assert.equal(await rows().filter({visible:true}).count(),await rootRows.count());
  assert.deepEqual(await rootRows.allTextContents(),totals.map(text=>text.replace('▾','▸')));
  await page.setViewportSize({width:390,height:844});await page.locator('#pivot-result').scrollIntoViewIfNeeded();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/pivot-inline-mobile.png'});
  config.view='table';await load();assert(await page.locator('#pivot-result .pivot-result-subtotal').count()>0);
  assert.deepEqual(errors,[]);console.log('Pivot inline totals: single and multiple measures/units, nested collapse/expand, grand totals, table compatibility and mobile passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
