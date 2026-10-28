const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.MOVEMENT_ACTION_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url+'?date='+c.day);
  await page.locator(`[data-housing-person="${c.person}"]`).getByRole('link',{name:'Заезд',exact:true}).click();
  const form=()=>page.locator('[data-movement-form]');
  assert(await page.locator('[data-housing-panel=movement]').isVisible());
  assert.equal(await form().locator('[name=camp]').inputValue(),'');
  assert.deepEqual(await form().locator('[name=employees]').evaluate(el=>[...el.selectedOptions].map(o=>o.value)),[String(c.employee)]);
  await form().locator('[name=camp]').selectOption(String(c.camp));
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Сохранить перемещение',exact:true}).click()]);
  const row=()=>page.locator(`[data-housing-person="${c.person}"]`);
  assert((await row().textContent()).includes('Базовый ВЖГ'));
  await row().getByRole('link',{name:'Выезд',exact:true}).click();
  await form().locator('[name=kind]').selectOption('DISMISSAL');
  assert.equal(await form().locator('[name=employees]').evaluate(el=>el.selectedOptions.length),1);
  assert(await form().locator('[name=target]').evaluate(el=>el.closest('.app-form-field').hidden));
  await page.screenshot({path:'/tmp/personnel-dismissal-reason.png',fullPage:true});
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Сохранить перемещение',exact:true}).click()]);
  assert((await row().textContent()).includes('Межвахтовый отдых'));
  await page.locator('[data-housing-tab=events]').click();
  assert((await page.locator('[data-housing-panel=events]').textContent()).includes('По увольнению'));
  assert.deepEqual(errors,[]);console.log('PASS: arrival without assigned camp, explicit camp selection and dismissal as exit reason');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
