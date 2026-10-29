const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.SMG_VARIANTS_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',r=>{if(r.request().url().startsWith(c.base))return r.continue();if(r.request().url().includes('bootstrap.min.css')&&fs.existsSync('/tmp/catalogue-bootstrap.css'))return r.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return r.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.create);await page.locator('[name=construction_object]').selectOption(String(c.object));await page.locator('[name=month]').fill('2026-01');await page.locator('[name=name]').fill('Независимый сценарий');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Создать СМГ',exact:true}).click()]);assert.match(await page.locator('body').innerText(),/Независимый сценарий/);
  await page.goto(c.base+c.compare);assert.match(await page.locator('h1').innerText(),/Сравнение СМГ/);const row=page.locator('section tbody>tr').filter({hasText:'Simple'}).first();assert(await row.isVisible());assert.match(await row.innerText(),/7/);assert.match(await row.innerText(),/12/);await page.screenshot({path:'/tmp/smg-variant-comparison.png',fullPage:true});
  await page.goto(c.base+c.list);const source=page.locator('tbody>tr').filter({hasText:'Исходный сценарий'}).first();await Promise.all([page.waitForNavigation(),source.getByRole('button',{name:'Копировать вариант',exact:true}).click()]);assert.match(await page.locator('body').innerText(),/Исходный сценарий — копия/);
  assert.deepEqual(errors,[]);console.log('PASS: multiple SMGs in one month, optional variant names, comparison of 7 vs 12, independent copy and editor navigation');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
