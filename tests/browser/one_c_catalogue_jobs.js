const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.ONEC_JOBS_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const card=kind=>page.locator(`[data-import-job="${kind}"]`);
  await page.goto(c.base+c.url);
  assert.equal(await page.locator('[data-import-job]').count(),2);
  assert(await card('employees').getByRole('button',{name:'Запустить сейчас'}).isEnabled());
  assert(await card('positions').getByRole('button',{name:'Запустить сейчас'}).isEnabled());
  const boxes=await page.locator('[data-import-job]').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().top));assert(Math.abs(boxes[0]-boxes[1])<3);
  for(const [kind,value] of [['employees','60'],['positions','1440']]){
   await card(kind).locator('[name=interval]').fill(value);
   await Promise.all([page.waitForNavigation(),card(kind).getByRole('button',{name:'Сохранить расписание'}).click()]);
  }
  assert.equal(await card('employees').locator('[name=interval]').inputValue(),'60');
  assert.equal(await card('positions').locator('[name=interval]').inputValue(),'1440');
  await page.screenshot({path:'/tmp/one-c-catalogue-jobs-wide.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert(await card('employees').evaluate(el=>el.getBoundingClientRect().bottom)<=await card('positions').evaluate(el=>el.getBoundingClientRect().top));
  await page.screenshot({path:'/tmp/one-c-catalogue-jobs-mobile.png',fullPage:true});
  await card('employees').getByRole('link',{name:'История обновлений сотрудников'}).click();
  assert.equal(new URL(page.url()).searchParams.get('kind'),'employees');
  await Promise.all([page.waitForNavigation(),card('employees').getByRole('button',{name:'Запустить сейчас'}).click()]);
  assert(await card('employees').getByRole('button',{name:'Запустить сейчас'}).isDisabled());
  assert(await card('positions').getByRole('button',{name:'Запустить сейчас'}).isDisabled());
  assert((await page.locator('tbody').textContent()).includes('Загрузка сотрудников'));
  assert.deepEqual(errors,[]);console.log('PASS: two 1C catalogue jobs, independent schedules, shared queue and mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
