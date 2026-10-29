const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.CATALOGUE_ALIAS_CONFIG);
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{if(response.url().startsWith(c.base)&&response.status()>=500)errors.push('HTTP '+response.status()+' '+response.url());});
  await page.goto(c.base+c.equipment_edit);
  const associations=page.locator('[data-excel-associations]');assert(await associations.isVisible());
  assert(!(await associations.evaluate(el=>Boolean(el.closest('details')))));
  await associations.locator('textarea').fill('ЭО-4225\nУниверсальная машина');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Сохранить',exact:true}).click()]);
  assert.equal(await page.locator('.errorlist').count(),0);
  await page.goto(c.base+c.equipment);
  await page.locator('input[name=name]').fill('универсальная   машина');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Применить',exact:true}).click()]);
  assert((await page.locator('.equipment-catalogue').innerText()).includes('Экскаватор гусеничный'));
  assert(!(await page.locator('.equipment-catalogue').innerText()).includes('Чужая техника'));
  await page.screenshot({path:'/tmp/equipment-excel-association-search.png',fullPage:true});
  for(const name of ['По технике для планирования','Таблица']){
   await Promise.all([page.waitForNavigation(),page.getByRole('link',{name,exact:true}).click()]);
   assert.equal(new URL(page.url()).searchParams.get('name'),'универсальная   машина');
   assert((await page.locator('.equipment-catalogue').innerText()).includes('Экскаватор гусеничный'));
  }
  await page.goto(c.base+c.jobs+'?name='+encodeURIComponent('ПРОРАБ'));
  assert.equal(await page.locator('[data-position-id]').count(),1);
  assert(await page.locator(`[data-position-id="${c.job}"]`).isVisible());
  async function chooseAlias(url,id,term,label,value){
   await page.goto(c.base+url);
   const native=page.locator('#'+id),shell=page.locator('#'+id+' + .search-select-shell');
   await shell.getByRole('combobox').click();
   const popup=page.locator('.search-select-popup:visible');
   await popup.locator('input[type=search]').fill(term);
   const option=popup.getByRole('option',{name:label,exact:true});assert(await option.isVisible());
   await option.click();assert.equal(await native.inputValue(),String(value));
   assert.equal((await shell.locator('.search-select-label').innerText()).trim(),label);
  }
  await chooseAlias(c.equipment_create,'id_parent','эо-422','Экскаватор гусеничный',c.root);
  await chooseAlias(c.employee_create,'id_position','РУКОВОДИТЕЛЬ смр','Мастер участка',c.position);
  assert.deepEqual(errors,[]);
  console.log('PASS: visible associations edit/save, equipment and job alias filters, preserved queries in all views, equipment/position dropdown alias search with original labels and IDs');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
