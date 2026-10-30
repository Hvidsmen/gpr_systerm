const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const c=JSON.parse(process.env.IMPORT_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),errors=[];
 try{
 async function pageFor(cookie){const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addCookies([{name:'sessionid',value:cookie,url:c.base}]);await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));return page;}
 const admin=await pageFor(c.cookie),other=await pageFor(c.second_cookie),query=`?camp=${c.camp}&date=${c.day}`;
 await admin.goto(c.base+c.upload);await admin.locator('[name=date]').fill(c.day);await admin.locator('[name=files]').setInputFiles([c.first,c.second]);await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Проверить файлы'}).click()]);
 assert((await admin.locator('main').textContent()).includes('Требуют исправления: 2'));
 await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Загрузить корректные записи и сохранить ошибки'}).click()]);
 await admin.goto(c.base+c.errors);assert.equal(await admin.getByRole('link',{name:'Исправить',exact:true}).count(),2);
 await admin.setViewportSize({width:390,height:844});assert(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await admin.screenshot({path:'/tmp/transport-import-errors-mobile.png',fullPage:true,animations:'disabled'});await admin.setViewportSize({width:1440,height:1000});
 await admin.getByRole('link',{name:'Исправить',exact:true}).first().click();await admin.screenshot({path:'/tmp/transport-import-correction.png',fullPage:true,animations:'disabled'});await admin.locator('[name=vin]').fill('VIN-200');await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Проверить и добавить машину'}).click()]);
 assert.equal(await admin.getByRole('link',{name:'Исправить',exact:true}).count(),1);await admin.getByRole('link',{name:'Исправить',exact:true}).click();await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Это та же машина — закрыть повтор'}).click()]);assert.equal(await admin.getByRole('link',{name:'Исправить',exact:true}).count(),0);
 await admin.goto(c.base+c.board+query);assert.equal(await admin.locator('[data-transport-row]').count(),2);
 const row=admin.locator('[data-transport-row]').first();
 assert.equal(await row.locator('[data-detail-field=repair_started_on]').getAttribute('type'),'date');
 const oldWorked=Number(await admin.locator('[data-transport-count=WORKED]').textContent()),oldRepair=Number(await admin.locator('[data-transport-count=REPAIR]').textContent());const change=admin.waitForResponse(r=>r.url().includes('/transport/cell/')&&r.request().method()==='POST');await row.locator('[data-detail-field=condition]').selectOption('REPAIR');assert((await change).ok());await row.locator('[data-cell-save-status]').filter({hasText:'Сохранено'}).waitFor();assert.equal(Number(await admin.locator('[data-transport-count=REPAIR]').textContent()),oldRepair+1);assert.equal(Number(await admin.locator('[data-transport-count=WORKED]').textContent()),oldWorked-1);const ready=admin.waitForResponse(r=>r.url().includes('/transport/cell/')&&r.request().method()==='POST');await row.locator('[data-detail-field=condition]').selectOption('READY');assert((await ready).ok());await row.locator('[data-cell-save-status]').filter({hasText:'Сохранено'}).waitFor();
 await row.locator('select[data-detail-field=parts_stage] + .search-select-shell button').click();
 const popup=admin.locator('.search-select-popup:not([hidden])');
 const saved=admin.waitForResponse(r=>r.url().includes('/transport/cell/')&&r.request().method()==='POST');
 await popup.getByRole('option',{name:'Доставлены',exact:true}).click();assert((await saved).ok());await row.locator('[data-cell-save-status]').filter({hasText:'Сохранено'}).waitFor();
 await row.locator('select[data-status-kind=PARTS]').locator('..').getByRole('button',{name:'Добавить значение в справочник'}).click();
 await admin.locator('[data-status-form] [name=name]').fill('На складе участка');
 const quick=admin.waitForResponse(r=>r.url().includes('/status-options/create/'));await admin.locator('[data-status-form]').getByRole('button',{name:'Добавить и выбрать'}).click();assert((await quick).ok());
 await row.locator('[data-cell-save-status]').filter({hasText:'Сохранено'}).waitFor();
 await admin.screenshot({path:'/tmp/transport-inline-fields.png',fullPage:false,animations:'disabled'});
 await admin.getByRole('button',{name:'Добавить машину',exact:true}).first().click();
 const machine=admin.locator('[data-new-machine-form]');await machine.locator('[name=new-machine-type]').selectOption(String(c.planning_type));await machine.locator('[name=new-machine-inventory_number]').fill('INLINE-NEW-1');
 await Promise.all([admin.waitForNavigation(),machine.getByRole('button',{name:'Добавить машину',exact:true}).click()]);assert.equal(await admin.locator('[data-transport-row]').count(),3);
 const fuelUrl=c.base+c.fuel+query+'&fuel_type=DIESEL';await admin.goto(fuelUrl);await other.goto(fuelUrl);
 await admin.locator('[name=opening]').fill('100');await admin.locator('[name=received]').fill('50');await admin.locator('[name=consumed]').fill('20');assert((await admin.locator('[data-fuel-preview]').textContent()).includes('130'));
 await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Сохранить баланс'}).click()]);assert((await admin.locator('[data-fuel-balance]').textContent()).includes('130'));
 await other.locator('[name=opening]').fill('100');await other.locator('[name=received]').fill('50');await other.locator('[name=consumed]').fill('25');await Promise.all([other.waitForNavigation(),other.getByRole('button',{name:'Сохранить баланс'}).click()]);assert((await other.locator('main').textContent()).includes('Баланс уже изменил другой диспетчер'));
 await other.goto(fuelUrl);await other.locator('[name=consumed]').fill('25');await Promise.all([other.waitForNavigation(),other.getByRole('button',{name:'Сохранить баланс'}).click()]);assert((await other.locator('[data-fuel-balance]').textContent()).includes('125'));
 const download=other.waitForEvent('download');await other.getByRole('link',{name:'Выгрузить Excel',exact:true}).click();assert((await download).suggestedFilename().endsWith('.xlsx'));
 await other.screenshot({path:'/tmp/camp-fuel-wide.png',fullPage:true,animations:'disabled'});await other.setViewportSize({width:390,height:844});assert(await other.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await other.screenshot({path:'/tmp/camp-fuel-mobile.png',fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);console.log('PASS: two-file import, duplicate VIN queue, correction and closing repeats, daily repair details, shared camp fuel balance, conflict prevention, history, Excel and mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
