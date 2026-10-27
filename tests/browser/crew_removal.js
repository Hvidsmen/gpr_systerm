const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.CREW_REMOVAL_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1720,height:1050}});
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   if(url.endsWith('/bootstrap-icons.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap-icons.css')});return route.abort();});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const load=url=>page.goto(c.base+url,{waitUntil:'networkidle'});
  const entry=label=>page.locator('[data-plan-entry][data-label="'+label+'"]');
  for(const [url,daily] of [[c.gpr,false],[c.smg,true]]){
   await load(url);
   assert.deepEqual((await page.locator('[data-smg-section=equipment] [data-plan-group-name]').allTextContents()).map(s=>s.trim()).sort(),['Землеройная техника','Подъёмная техника']);
   const save=()=>Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),daily?page.locator('#smg-save-table').click():page.locator('[data-plan-action-header] button[form=allocation-editor][value=save]').click()]);
   await entry('Машинист').locator('[data-plan-delete]').click();
   const dialog=page.locator('[data-plan-delete-dialog]');
   assert.equal(await dialog.locator('[data-plan-delete-machines] li').count(),2);
   await dialog.locator('[data-plan-close-dialog]').click();assert(await entry('Экскаватор').isVisible());
   await entry('Машинист').locator('[data-plan-delete]').click();
   await page.screenshot({path:daily?'/tmp/smg-crew-removal.png':'/tmp/gpr-crew-removal.png'});
   if(daily)await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),dialog.locator('[data-plan-delete-mode=detach]').click()]);
   else{await dialog.locator('[data-plan-delete-mode=detach]').click();assert(await entry('Экскаватор').isVisible());await save();}
   assert.equal(await entry('Машинист').count(),0);assert.equal(await page.locator('[data-smg-section=equipment] [data-plan-entry]').count(),2);
   await entry('Экскаватор').locator(daily?'.smg-day-input':'[data-field=count]').first().fill('3');await save();
   assert.equal(await entry('Машинист').count(),0,'Detached position must not return on recalculation');
   await entry('Помощник').locator('[data-plan-delete]').click();
   if(daily)await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),dialog.locator('[data-plan-delete-mode=remove_equipment]').click()]);
   else{await dialog.locator('[data-plan-delete-mode=remove_equipment]').click();assert.equal(await page.locator('[data-smg-section=equipment] [data-plan-entry]:visible').count(),0);await save();}
   assert.equal(await page.locator('[data-smg-section=equipment] [data-plan-entry]').count(),0);assert.equal(await entry('Помощник').count(),0);
  }
  await load(c.catalogue);
  assert.equal(await page.locator('[data-equipment-category]').count(),2);
  assert(await page.locator('#bulk-delete-form').isHidden());
  await page.locator('[data-equipment-fold=open]').click();
  const group=page.locator('[data-equipment-category]').filter({has:page.locator('[data-equipment-type]').filter({hasText:'Экскаватор'})});
  await group.locator('[data-equipment-select-category]').click();
  assert(await page.locator('#bulk-delete-form').isVisible());assert.equal(await page.locator('[data-bulk-row]:checked').count(),3);
  await page.locator('[data-bulk-clear]').click();assert(await page.locator('#bulk-delete-form').isHidden());
  await page.locator('[data-equipment-select-page]').click();assert.equal(await page.locator('[data-bulk-row]:checked').count(),4);
  await page.locator('[data-bulk-clear]').click();
  await page.locator('.equipment-catalogue-menu>summary').click();assert(await page.locator('.equipment-catalogue-menu-body').isVisible());
  await page.keyboard.press('Escape');assert(await page.locator('.equipment-catalogue-menu-body').isHidden());
  await page.screenshot({path:'/tmp/equipment-catalogue-wide.png',fullPage:false});
  await page.locator('[data-equipment-fold=close]').click();await page.reload({waitUntil:'networkidle'});
  assert((await page.locator('[data-equipment-category]').evaluateAll(nodes=>nodes.every(n=>!n.open))));
  await load(c.catalogue+'?name=CAT');
  assert.equal(await page.locator('[data-bulk-row]').count(),1,'Parent context must not be selected when searching for a model');
  assert(await page.locator('[data-equipment-type]').evaluate(n=>n.open));
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.getElementById('sidebar').getBoundingClientRect().right<=1);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'Catalogue must not overflow on a phone');
  await page.screenshot({path:'/tmp/equipment-catalogue-mobile.png',fullPage:false});
  await load(c.catalogue+'?view=table&is_active=False');
  assert.equal(await page.locator('[data-bulk-row]').count(),1);assert.equal(await page.locator('input[name=view]').inputValue(),'table');
  assert.deepEqual(errors,[]);console.log('Crew removal: cancel, detach, cascade, category totals; catalogue: category selection, filters, fold persistence, desktop and mobile passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
