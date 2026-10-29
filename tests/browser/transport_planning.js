const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.TRANSPORT_PLANNING_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[],employeeRequests=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',response=>{if(response.url().includes('employee-options'))employeeRequests.push({url:response.url(),status:response.status()});});
  await page.goto(c.base+c.url+`?camp=${c.camp}&date=${c.day}`);
  const type=page.locator(`[data-transport-type="${c.type}"]`),row=page.locator(`[data-transport-row="${c.machine}"]`);
  async function ready(){await page.waitForFunction(()=>document.querySelector('[data-transport-board]')?.dataset.planningReady==='1');}
  async function assign(id){await ready();await type.locator('[data-type-add]').click();const form=type.locator('[data-type-picker]');assert((await form.locator('[name=equipment] option').filter({hasText:'Crane'}).first().innerText()).startsWith('Crane · '));if(id===c.machine){await form.locator('[name=equipment] + .search-select-shell button').first().click();await page.screenshot({path:'/tmp/transport-planning-type-picker.png'});await page.keyboard.press('Escape');}await form.locator('[name=equipment]').selectOption(String(id));await Promise.all([page.waitForNavigation(),form.getByRole('button',{name:'Добавить в факт',exact:true}).click()]);await ready();}
  async function saved(target=row){await target.locator('[data-cell-save-status]').first().filter({hasText:'Сохранено'}).waitFor();}
  await ready();assert.deepEqual(await page.locator('[data-transport-group]').evaluateAll(nodes=>nodes.map(n=>n.dataset.transportGroup)),['Специализированная техника']);
  assert.equal(await page.locator('[data-transport-type]').count(),2);assert.equal(await page.locator('[data-transport-row]').count(),0);
  await assign(c.machine);assert.equal(await row.locator('[data-detail-field=status]').inputValue(),'WORKED');assert.equal(await type.locator('[data-type-worked]').innerText(),'1');assert.equal(await type.locator('[data-type-remaining]').innerText(),'2');
  await row.locator('[data-detail-field=target]').selectOption('object:'+c.object);await saved();
  const driver=row.locator('[data-detail-field=shift_one_employee]');await row.locator('[data-detail-field=shift_one_employee] + .search-select-shell button').first().click();await driver.locator(`option[value="${c.employee}"]`).waitFor({state:'attached',timeout:10000}).catch(async error=>{console.error({employeeRequests,field:await driver.evaluate(el=>({html:el.outerHTML,previous:el.previousElementSibling?.outerHTML,next:el.nextElementSibling?.outerHTML})),messages:await row.locator('[data-cell-save-status]').allTextContents(),errors});throw error;});await driver.selectOption(String(c.employee));await saved();await page.keyboard.press('Escape');
  await assign(c.second);assert.equal(await type.locator('[data-type-worked]').innerText(),'2');assert.equal(await type.locator('[data-type-remaining]').innerText(),'1');
  const second=page.locator(`[data-transport-row="${c.second}"]`);await second.locator('[data-detail-field=status]').selectOption('IDLE');await saved(second);assert.equal(await type.locator('[data-type-worked]').innerText(),'1');assert.equal(await type.locator('[data-type-idle]').innerText(),'1');
  await Promise.all([page.waitForNavigation(),second.locator('[data-type-remove]').click()]);await ready();assert.equal(await page.locator('[data-transport-row]').count(),1);assert.equal(await type.locator('[data-type-picker] option').count(),2);
  await page.getByRole('link',{name:'Состояние и ремонт',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-detail-field=condition]')?.dataset.saved!==undefined);await row.locator('[data-detail-field=condition]').selectOption('REPAIR');await saved();
  await row.locator('[data-repair-dialog]').waitFor({state:'visible'});await row.locator('[data-repair-dialog]').getByRole('button',{name:'Готово',exact:true}).click();await row.locator('[data-repair-dialog]').waitFor({state:'hidden'});
  await page.getByRole('link',{name:'Работа за день',exact:true}).click();await ready();assert.equal(await driver.inputValue(),String(c.employee));assert.equal(await row.locator('[data-detail-field=status]').inputValue(),'WORKED');
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'/tmp/transport-planning-wide.png',fullPage:true});
  const top=page.locator('[data-transport-top-scroll]'),viewport=page.locator('[data-transport-table-scroll]');await top.evaluate(el=>el.scrollLeft=200);await page.waitForFunction(()=>Math.abs(document.querySelector('[data-transport-table-scroll]').scrollLeft-200)<2);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:'/tmp/transport-planning-mobile.png',fullPage:true});
  await page.setViewportSize({width:1530,height:1040});await page.locator('[data-add-outside]').click();const outside=page.locator('[data-outside-dialog]');await outside.waitFor({state:'visible'});
  await outside.locator('[data-outside-type]').selectOption(String(c.bus_type));assert((await outside.locator('[name=equipment] option').last().innerText()).startsWith('Автобус вахтовый · '));await outside.locator('[name=equipment]').selectOption(String(c.bus));
  await Promise.all([page.waitForNavigation(),outside.getByRole('button',{name:'Добавить в факт',exact:true}).click()]);await ready();
  assert.equal(await page.locator('[data-planned-equipment] [data-transport-type]').count(),2);assert(await page.locator('[data-outside-facts]').evaluate(el=>el.open));assert.equal(await page.locator(`[data-outside-facts] [data-transport-row="${c.bus}"]`).count(),1);
  await page.reload();await ready();assert.equal(await page.locator('[data-outside-facts]').evaluate(el=>el.open),false);await page.locator('[data-select-all]').check();assert.equal(await page.locator(`[data-outside-facts] [data-select-machine][value="${c.bus}"]`).isChecked(),false);await page.locator('[data-select-all]').uncheck();await page.locator('[data-outside-facts] > summary').click();
  await Promise.all([page.waitForNavigation(),page.locator(`[data-outside-facts] [data-transport-row="${c.bus}"] [data-type-remove]`).click()]);await ready();assert.equal(await page.locator('[data-outside-facts]').count(),0);
  await page.locator('[data-transport-view=list]').click();assert.equal(await page.locator('[data-transport-row]').count(),3);assert.equal(await page.locator('[data-transport-type]').count(),0);
  await page.waitForLoadState('load');
  assert.equal(await row.locator('[data-detail-field=condition]').count(),1);
  for(const field of ['status','target','day_comment','shift_one_employee','shift_two_employee','in_gpr'])assert.equal(await row.locator(`[data-detail-field="${field}"]`).count(),0);
  assert.equal(await page.locator('[data-edit-machine],[data-transport-action=DAY],[data-copy-previous=equipment]').count(),0);
  assert.notEqual((await row.locator('[data-label="Где работала"]').innerText()).trim(),'—');
  assert.notEqual(await row.locator('[data-label="1-я смена · водитель / машинист"]').innerText(),'—');
  const inlineUrl=await page.locator('[data-transport-board]').getAttribute('data-inline-url');
  for(const condition of ['READY','REPAIR']){
   const response=page.waitForResponse(r=>r.url().endsWith(inlineUrl)&&r.request().method()==='POST');
   await row.locator('[data-detail-field=condition]').selectOption(condition);assert.equal((await (await response).json()).condition,condition);await saved();
  }
  await page.reload();await page.waitForLoadState('load');
  assert.equal(await row.locator('[data-detail-field=condition]').inputValue(),'REPAIR');
  assert.match(await row.locator('[data-label="За день"]').innerText(),/Работала/);
  await page.screenshot({path:'/tmp/transport-reference-list.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: group/type hierarchy, planned-only equipment, actual assignment, target, shift driver, live counts, idle, clearing, independent repair, reference-only list with editable condition, scrolling and mobile');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
