const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.QUICK_BED_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url+`?camp=${c.camp}&date=${c.day}`);const originalUrl=page.url();
  await page.locator('#movement').evaluate(el=>el.open=true);
  const form=page.locator('[data-lodging-movement]');
  await form.locator('[name=employee]').selectOption(String(c.employee));await form.locator('[name=bed]').selectOption(String(c.bed));
  await form.locator('[data-lodging-take]').click();await form.locator('[data-lodging-lock-label]').filter({hasText:'Вы оформляете'}).waitFor();
  await form.locator('[name=pass_number]').fill('PASS-KEEP');await form.locator('[name=comment]').fill('Сохранить введённое');
  const quick=page.locator('[data-lodging-quick-bed]');await quick.locator('summary').click();
  await quick.locator('[name=new_building_name]').fill('Вагон новый');await quick.locator('[name=room]').fill('3');
  await quick.getByRole('button',{name:'Создать и выбрать место'}).click();
  // HTML validation catches a missing number without navigating or losing data.
  assert.equal(page.url(),originalUrl);assert(await quick.locator('[name=number]').evaluate(el=>!el.validity.valid));
  await quick.locator('[name=number]').fill('7');await page.screenshot({path:'/tmp/lodging-quick-place-wide.png',fullPage:true});
  await quick.getByRole('button',{name:'Создать и выбрать место'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-lodging-quick-bed]').open===false);
  assert.equal(page.url(),originalUrl);assert((await form.locator('[name=bed] option:checked').textContent()).includes('Вагон новый · комн. 3 · место 7'));
  assert.equal(await form.locator('[name=employee]').inputValue(),String(c.employee));
  assert.equal(await form.locator('[name=pass_number]').inputValue(),'PASS-KEEP');assert.equal(await form.locator('[name=comment]').inputValue(),'Сохранить введённое');
  await form.locator('[data-lodging-take]').click();await form.locator('[data-lodging-lock-label]').filter({hasText:'Вы оформляете'}).waitFor();
  assert(await form.evaluate(el=>el.checkValidity()));
  await Promise.all([page.waitForNavigation(),page.locator('[data-lodging-save]').click()]);
  assert((await page.locator('[data-lodging-bed][data-employee="'+c.employee+'"]').textContent()).includes('PASS-KEEP'));
  await page.setViewportSize({width:390,height:844});await page.locator('#movement').evaluate(el=>el.open=true);await quick.locator('summary').click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:'/tmp/lodging-quick-place-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: inline building/place creation, retained inputs, claim renewal, save and mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
