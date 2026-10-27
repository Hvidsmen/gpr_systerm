const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.APPROVED_GROUPS_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1720,height:1050}});
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   if(url.endsWith('/bootstrap-icons.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap-icons.css')});return route.abort();});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(c.base+c.url,{waitUntil:'networkidle'});
  const draft=c.stage==='draft';
  const equipment=page.locator(draft?'[data-smg-section=equipment]':'[data-matrix-section=equipment]');
  const names=await equipment.locator(draft?'[data-plan-group-name]':':scope > details.matrix-group > summary').allTextContents();
  assert.equal(names.length,2);
  assert(names[0].trim().startsWith('Специализированная техника'));
  assert(names[1].trim().startsWith('Вспомогательная техника'));
  const people=page.locator(draft?'[data-smg-section=labor]':'[data-matrix-section=labor]');
  const groups=await people.locator(draft?'[data-plan-group-name]':':scope > details.matrix-group > summary').allTextContents();
  assert(groups.some(name=>name.trim().startsWith('ИТР Состав')));
  assert(!groups.some(name=>name.trim().startsWith('Без группы')));
  if(!draft){
   const works=page.locator('[data-matrix-section=works]');
   assert((await works.locator('details.matrix-group > summary').allTextContents()).some(name=>name.includes('УЗА 33')));
   await page.locator('[data-work-toggle]').first().click();
   assert(await page.locator('.matrix-child:visible').count()>0);
   const select=page.locator('#id_equipment_category');
   const id=await select.locator('option').filter({hasText:'Специализированная техника'}).getAttribute('value');
   await select.selectOption(id,{force:true});
   await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.locator('form').filter({has:page.locator('#id_equipment_category')}).getByRole('button',{name:'Применить',exact:true}).click()]);
   const filtered=await page.locator('[data-matrix-section=equipment] > details.matrix-group > summary').allTextContents();
   assert.equal(filtered.length,1);assert(filtered[0].trim().startsWith('Специализированная техника'));
  }
  await equipment.scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/gpr-categories-'+c.stage+'.png'});
  assert.deepEqual(errors,[]);console.log('Current catalogue groups and equipment categories verified in '+c.stage+' GPR, including legacy assignments.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
