const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.CONFIRMED_STAFFING_CONFIG);
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(c.base+c.list);
  assert.equal(await page.locator('[data-position-id]').count(),1);
  assert(await page.locator(`[data-position-id="${c.current}"]`).isVisible());
  await Promise.all([page.waitForNavigation(),page.locator('[data-confirmed-staffing-link]').click()]);
  assert.equal(new URL(page.url()).searchParams.get('staffing_current'),'confirmed');
  assert.equal(await page.locator('[data-position-id]').count(),2);
  let row=page.locator(`[data-position-id="${c.manual}"]`);
  assert.equal(await row.locator('.position-staffing-status').innerText(),'Подтверждена ответственным · отсутствует в ЗУП');
  assert((await page.locator(`[data-position-id="${c.retired}"]`).innerText()).includes('неактуальна в ЗУП'));
  assert(await page.locator('[data-confirmed-staffing-help]').isVisible());
  await page.screenshot({path:'/tmp/confirmed-staffing-groups.png',fullPage:true});
  await Promise.all([page.waitForNavigation(),page.getByRole('link',{name:'Таблица',exact:true}).click()]);
  assert.equal(new URL(page.url()).searchParams.get('staffing_current'),'confirmed');
  assert.equal(await page.locator('[data-position-id]').count(),2);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/confirmed-staffing-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await Promise.all([page.waitForNavigation(),page.locator(`[data-position-id="${c.manual}"]`).getByRole('link',{name:'Изменить',exact:true}).click()]);
  assert((await page.locator('body').innerText()).includes('Подтверждена ответственным · отсутствует в ЗУП'));
  assert((await page.locator('body').innerText()).includes('Должность доступна для планирования по решению ответственного'));
  assert.deepEqual(errors,[]);
  console.log('PASS: current staffing default, confirmed shortcut/filter, both catalogue views, source labels, mobile width and edit card');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
