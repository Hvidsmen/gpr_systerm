const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.TRANSPORT_REPAIR_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url+`?camp=${c.camp}&date=${c.day}`);
  const row=page.locator(`[data-transport-row="${c.machine}"]`),dialog=row.locator('[data-repair-dialog]');
  const field=name=>row.locator(`[data-detail-field="${name}"]`);
  async function saved(){try{await row.locator('[data-cell-save-status]').first().filter({hasText:'Сохранено'}).waitFor({timeout:10000});}catch(e){console.error(await row.locator('[data-cell-save-status]').allTextContents(),errors);throw e;}}
  assert.equal(await page.locator('.transport-table thead th').count(),10);
  assert.equal(await field('defects').isVisible(),false);assert.equal(await field('shift_one_employee').isDisabled(),true);
  await field('status').selectOption('WORKED');await saved();assert.equal(await field('shift_one_employee').isDisabled(),false);
  await row.locator('[data-detail-field=shift_one_employee] + .search-select-shell button').first().click();
  await field('shift_one_employee').locator(`option[value="${c.employee}"]`).waitFor({state:'attached'});
  await field('shift_one_employee').selectOption(String(c.employee));await saved();await page.keyboard.press('Escape');
  const initial=page.url();await row.locator('[data-open-repair]').click();await dialog.waitFor({state:'visible'});assert.equal(page.url(),initial);
  assert.equal(await dialog.locator('legend').count(),3);assert(await dialog.getByText('Заявки и запчасти',{exact:true}).isVisible());
  await field('defects').fill('Проверить датчик давления');await field('defects').press('Tab');await saved();
  await field('request_details').fill('№ 123 · запчасти');await field('request_details').press('Tab');await saved();
  const add=field('request_stage').locator('..').getByRole('button',{name:'Добавить значение в справочник'});await add.click();
  const status=page.locator('[data-status-dialog]');await status.waitFor({state:'visible'});await status.locator('[name=name]').fill('Проверено механиком');await status.getByRole('button',{name:'Добавить и выбрать'}).click();await status.waitFor({state:'hidden'});await saved();
  await dialog.screenshot({path:'/tmp/transport-repair-dialog.png'});
  await dialog.getByRole('button',{name:'Готово',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.equal(page.url(),initial);
  await page.getByRole('link',{name:'Состояние и ремонт',exact:true}).click();
  assert.equal(await page.locator('[data-detail-field=status]').count(),0);assert.equal(await page.locator('[data-detail-field=shift_one_employee]').count(),0);
  assert.equal(await page.locator('[data-transport-action=CONDITION]').count(),0);assert.equal(await row.locator('[data-edit-machine]').count(),0);assert.equal(await dialog.locator('[data-detail-field=condition]').count(),0);
  await page.waitForFunction(()=>document.querySelector('[data-detail-field=condition]')?.dataset.saved!==undefined);await field('condition').selectOption('REPAIR');await saved();assert.equal(await page.locator('dialog[open]').count(),0);assert.equal(await dialog.locator('[data-condition-label]').innerText(),'В ремонте');
  assert((await row.locator('[data-request-summary]').innerText()).includes('Проверено механиком'));assert.equal(await page.locator('[data-transport-count=READY]').innerText(),'0');assert.equal(await page.locator('[data-transport-count=REPAIR]').innerText(),'1');
  await page.screenshot({path:'/tmp/transport-state-workflow.png',fullPage:true});
  await row.locator('[data-open-repair]').click();assert.equal(await field('defects').inputValue(),'Проверить датчик давления');
  await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
  await page.reload();assert.equal(await field('condition').inputValue(),'REPAIR');
  await page.getByRole('link',{name:'Работа за день',exact:true}).click();assert.equal(await field('status').inputValue(),'WORKED');assert.equal(await field('shift_one_employee').inputValue(),String(c.employee));assert.equal(await row.locator('[data-condition-label]').first().innerText(),'В ремонте');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+2,{},{timeout:3000}).catch(async error=>{await page.screenshot({path:'/tmp/transport-repair-mobile-overflow.png',fullPage:true});console.error(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,wide:[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+2&&getComputedStyle(el).position!=='fixed').slice(0,15).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))})));throw error;});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await row.locator('[data-open-repair]').click();await dialog.waitFor({state:'visible'});
  assert(await dialog.evaluate(el=>el.getBoundingClientRect().width<=innerWidth-12));await dialog.screenshot({path:'/tmp/transport-repair-mobile.png'});
  await dialog.getByRole('button',{name:'Готово',exact:true}).click();await dialog.waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);console.log('PASS: separate transport workflows, compact daily columns, daily driver assignment, repair overlay, inline save, status creation, persistence, Escape, desktop and mobile');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
