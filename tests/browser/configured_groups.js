const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.CONFIGURED_GROUPS_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1500,height:1050},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(c.base+c.order);
  await page.locator('[data-group-order-editor] > summary').click();
  await page.locator('[data-group-order-row]').filter({hasText:'А должности'}).locator('input[type=number]').fill('0');
  await page.locator('[data-group-order-row]').filter({hasText:'Я должности'}).locator('input[type=number]').fill('90');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Сохранить порядок',exact:true}).click()]);
  assert.equal(await page.locator('.errorlist').count(),0);
  await page.goto(c.base+c.single);
  assert.deepEqual((await page.locator('[data-smg-section=labor] [data-plan-group-name]').allTextContents()).map(s=>s.trim()),['А должности','Я должности']);
  for(const kind of ['labor','equipment']){
   const groups=page.locator(`[data-smg-section=${kind}] [data-smg-group]`);assert.equal(await groups.count(),2);
   for(const group of await groups.all()){await group.evaluate(el=>el.open=false);assert(await group.locator('summary [data-plan-group-totals]').isVisible());assert((await group.locator('summary').innerText()).includes('Итого'));}
  }
  await page.screenshot({path:'/tmp/configured-groups-single.png',fullPage:true});
  await page.goto(c.base+c.months);
  assert.deepEqual((await page.locator('[data-smg-section=labor] [data-plan-group-name]').allTextContents()).map(s=>s.trim()),['А должности','Я должности']);
  const group=page.locator('[data-smg-section=labor] [data-grid-resource-group]').first();
  await page.waitForFunction(()=>{const head=document.querySelector('[data-grid-month="2026-01-01"]'),total=document.querySelector('[data-grid-total-month="2026-01-01"]');return Math.abs(head.getBoundingClientRect().left-total.getBoundingClientRect().left)<2;});
  await group.evaluate(el=>el.open=false);assert(await group.locator('[data-grid-total-month="2026-01-01"]').isVisible());
  assert.equal(await group.locator('[data-grid-total-month="2026-01-01"] [data-grid-total-number]').innerText(),'2');
  await group.evaluate(el=>el.open=true);
  await group.locator('[data-month="2026-01-01"] [data-grid-input]').fill('7');
  await page.waitForFunction(()=>document.querySelector('[data-smg-section=labor] [data-grid-total-number]').textContent==='7');
  const other=page.locator('[data-smg-section=equipment] [data-grid-resource-group]').first();
  assert.equal(await other.locator('[data-grid-total-month="2026-01-01"] [data-grid-total-number]').innerText(),'0');
  await page.screenshot({path:'/tmp/configured-groups-months.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/configured-groups-mobile.png',fullPage:true});
  // Discard this test edit through a fresh page to preserve the browser's dirty-form guard.
  await group.locator('[data-month="2026-01-01"] [data-grid-input]').fill('2');
  await page.setViewportSize({width:1500,height:1050});
  await page.goto(c.base+c.smg);
  for(const kind of ['labor','equipment']){
   const groups=page.locator(`[data-smg-section=${kind}] [data-smg-group]`);assert.equal(await groups.count(),2);
   for(const item of await groups.all()){await item.evaluate(el=>el.open=false);assert(await item.locator('summary [data-plan-group-totals]').isVisible());assert((await item.locator('summary').innerText()).includes('Итого'));}
  }
  await page.screenshot({path:'/tmp/configured-groups-smg.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: shared order editor, GPR single/months and SMG order, labor/equipment collapsed totals including zero, live monthly recalculation and mobile width');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
