const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.BULK_GROUP_BROWSER_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   if(url.endsWith('/bootstrap-icons.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap-icons.css')});return route.abort();});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.list+'?view=table&name='+encodeURIComponent('Электромонтажник'),{waitUntil:'networkidle'});
  const open=page.locator('[data-bulk-group-open]'),dialog=page.locator('[data-bulk-group-dialog]');
  assert(await open.isDisabled());assert.equal(await page.locator('[data-bulk-row]').count(),2);
  await page.locator('[data-position-select-all]').check();assert(!(await open.isDisabled()));
  await page.locator('[data-bulk-clear]').click();assert(await open.isDisabled());
  await page.locator('[data-bulk-row]').first().check();await page.locator('[data-bulk-row]').last().check();
  await open.click();assert((await dialog.locator('[data-bulk-group-count]').innerText()).includes('2'));
  assert.equal(await dialog.locator('input[name=selected]').count(),2);
  await dialog.locator('#id_assignment-group + .search-select-shell .search-select-button').click();
  const popup=dialog.locator('.search-select-popup:not([hidden])');await popup.locator('input').fill('Монтаж');
  await popup.getByRole('option',{name:'Монтаж',exact:true}).click();
  await page.screenshot({path:'/tmp/brigade-bulk-group-dialog.png'});
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),dialog.getByRole('button',{name:'Применить к выбранным',exact:true}).click()]);
  assert(page.url().includes('name='));assert.equal(await page.locator('tbody tr').count(),2);
  assert.equal(await page.locator('tbody tr td:nth-child(3)').allTextContents().then(v=>v.every(t=>t.trim()==='Монтаж')),true);
  assert(await page.getByText('Группы сохранены.',{exact:false}).isVisible());
  await page.setViewportSize({width:390,height:844});await page.locator('[data-position-select-all]').check();await open.click();
  const rect=await dialog.boundingBox();assert(rect.x>=0&&rect.x+rect.width<=391);
  await dialog.locator('#id_assignment-group + .search-select-shell .search-select-button').click();
  await popup.getByRole('option',{name:'Без группы',exact:true}).click();
  await page.screenshot({path:'/tmp/brigade-bulk-group-mobile.png'});
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),dialog.getByRole('button',{name:'Применить к выбранным',exact:true}).click()]);
  assert((await page.locator('tbody tr td:nth-child(3)').allTextContents()).every(t=>t.trim()==='Без группы'));
  assert.deepEqual(errors,[]);console.log('Bulk position groups: selection, search in modal, CSRF save, filters, preserve/assign/clear, planner access and mobile passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
