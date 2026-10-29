const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const c=JSON.parse(process.env.TABLE_SHIFTS_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const context=await browser.newContext({viewport:{width:1500,height:1000},locale:'ru-RU'});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await context.route('**/*',route=>{const u=route.request().url();if(u.startsWith(c.base))return route.continue();if(u.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const mode of ['gpr','smg']){
  await page.goto(c.base+c[mode]);await page.locator('[data-plan-add-resource=equipment]').click();
  const dialog=page.locator('[data-plan-add-dialog]');assert(await dialog.locator('[name=shifts_per_day]').isVisible());
  assert.equal(await dialog.locator('[name=shifts_per_day]').inputValue(),'1');await dialog.locator('[name=shifts_per_day]').fill('2');
  await dialog.locator('[name=equipment]').selectOption(String(c.gear),{force:true});
  await Promise.all([page.waitForNavigation(),dialog.getByRole('button',{name:'Добавить в план',exact:true}).click()]);
  const gear=page.locator('[data-smg-section=equipment] [data-plan-entry]').first();
  if(mode==='gpr')await gear.locator('[data-field=count]').fill('3');else await gear.locator('[data-smg-fill-month]').fill('3');
  const person=page.locator('[data-smg-section=labor] [data-plan-entry]').first();
  await page.waitForFunction(mode=>{const row=document.querySelector('[data-smg-section=labor] [data-plan-entry]');return row?.querySelector(mode==='gpr'?'[data-field=count]':'[data-field=planned_count]')?.value==='6';},mode);
  if(mode==='gpr')await Promise.all([page.waitForNavigation(),page.locator('button[form=allocation-editor]').first().click()]);else{await page.locator('#smg-save-table').click();await page.waitForFunction(()=>document.querySelector('#smg-save-status').dataset.state==='saved');}
  await page.screenshot({path:'/tmp/two-shifts-'+mode+'.png',fullPage:true});
 }
 assert.deepEqual(errors,[]);console.log('PASS: shifts default 1, add equipment with 2 shifts in GPR/SMG, 3 machines produce 6 people per crew position, live display and saved values');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
