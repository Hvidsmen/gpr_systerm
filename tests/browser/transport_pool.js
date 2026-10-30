const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const c=JSON.parse(process.env.TRANSPORT_POOL_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const ctx=await browser.newContext({viewport:{width:1500,height:1000},locale:'ru-RU'});await ctx.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await ctx.route('**/*',r=>{const u=r.request().url();if(u.startsWith(c.base))return r.continue();if(u.includes('bootstrap')&&u.includes('.css'))return r.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return r.abort();});
 const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));const url=tab=>c.base+c.url+`?camp=${c.camp}&date=${c.day}&tab=${tab}`;
 await p.goto(url('day'));await p.waitForFunction(()=>document.querySelector('[data-transport-board]').dataset.planningReady==='1');
 assert.equal(await p.locator('.transport-summary,[data-new-machine],[data-transport-action=ARRIVAL],[data-transport-action=TRANSFER],[data-transport-action=DEPARTURE],[data-transport-action=WRITEOFF],[data-transport-view]').count(),0);
 const card=id=>p.locator(`[data-transport-type="${id}"]`);
 await card(c.type).locator('[data-type-add]').click();let picker=card(c.type).locator('[data-type-picker]');
 assert.deepEqual(await picker.locator('optgroup').evaluateAll(groups=>groups.map(g=>g.label)),['Нужный тип · этот ВЖГ','Нужный тип · другие ВЖГ','Нужный тип · без оформленного ВЖГ','Остальная техника · этот ВЖГ']);
 assert.equal(await picker.locator(`option[value="${c.remote}"]`).count(),1);assert.equal(await picker.locator(`option[value="${c.bus}"]`).count(),1);
 await picker.locator('[name=equipment]').selectOption(String(c.remote));await Promise.all([p.waitForNavigation(),picker.getByRole('button',{name:'Добавить в факт',exact:true}).click()]);
 await card(c.empty).locator('[data-type-add]').click();picker=card(c.empty).locator('[data-type-picker]');await picker.locator('[name=equipment]').selectOption(String(c.second));await Promise.all([p.waitForNavigation(),picker.getByRole('button',{name:'Добавить в факт',exact:true}).click()]);
 assert.equal(await card(c.empty).locator(`[data-transport-row="${c.second}"]`).count(),1);assert.equal(await card(c.empty).locator('[data-type-worked]').innerText(),'1');
 assert.match(await card(c.empty).locator('[data-machine-whereabouts]').innerText(),/не оформлено/);
 await p.screenshot({path:'/tmp/transport-whole-pool-day.png',fullPage:true});
 await p.goto(url('state'));assert.equal(await p.locator('[data-transport-action=ARRIVAL]').count(),0);
 await p.locator(`[data-select-machine][value="${c.machine}"]`).check();const dialog=p.locator('[data-action-dialog]');
 for(const action of ['TRANSFER','DEPARTURE','WRITEOFF']){await p.locator(`[data-transport-action="${action}"]`).click();await dialog.waitFor({state:'visible'});assert.deepEqual(await dialog.locator('[name=equipment] option').evaluateAll(items=>items.map(i=>i.value)),[String(c.machine)]);await dialog.getByRole('button',{name:'Отмена',exact:true}).click();}
 for(const action of ['TRANSFER','DEPARTURE','WRITEOFF']){await p.locator(`[data-transport-row="${c.bus}"] [data-machine-action="${action}"]`).click();await dialog.waitFor({state:'visible'});assert.deepEqual(await dialog.locator('[name=equipment] option').evaluateAll(items=>items.map(i=>i.value)),[String(c.bus)]);await dialog.getByRole('button',{name:'Отмена',exact:true}).click();}
 await p.locator('[data-new-machine]').click();const machine=p.locator('[data-new-machine-dialog]'),form=machine.locator('form');await machine.waitFor({state:'visible'});
 assert.equal(await form.locator('[name=new-machine-name]').count(),0);await form.locator('[name=new-machine-type]').selectOption(String(c.type));await form.locator('[name=new-machine-model]').selectOption(String(c.model));
 assert(await form.locator('[name=new-machine-brand_model]').isDisabled());assert.equal(await form.locator('[name=new-machine-brand_model]').inputValue(),'CAT 320');
 await form.locator('[name=new-machine-model]').selectOption('');assert.equal(await form.locator('[name=new-machine-brand_model]').isDisabled(),false);
 await form.locator('[name=new-machine-inventory_number]').fill('BROWSER-UNIT-NEW');await form.locator('[data-propose-planning-type]').click();const proposal=p.locator('[data-propose-type-dialog]');await proposal.waitFor({state:'visible'});
 await proposal.locator('[name=name]').fill('Browser new planning type');await proposal.locator('[name=group]').selectOption(String(c.group));await proposal.getByRole('button',{name:'Создать и отправить на проверку'}).click();await proposal.waitFor({state:'hidden'});
 assert.match(await form.locator('[name=new-machine-type] option:checked').innerText(),/На проверке/);assert.equal(await form.locator('[name=new-machine-inventory_number]').inputValue(),'BROWSER-UNIT-NEW');
 await form.locator('[name=new-machine-receive]').uncheck();await p.screenshot({path:'/tmp/transport-machine-create-review.png',fullPage:true});
 const response=p.waitForResponse(r=>r.url().includes('/transport/create/')&&r.request().method()==='POST');await form.getByRole('button',{name:'Добавить машину',exact:true}).click();const result=await (await response).json();assert.equal(result.review,'PENDING');await p.waitForLoadState('load');
 const reviewCtx=await browser.newContext();await reviewCtx.addCookies([{name:'sessionid',value:c.reviewer_cookie,url:c.base}]);await reviewCtx.route('**/*',r=>r.request().url().startsWith(c.base)?r.continue():r.abort());const r=await reviewCtx.newPage();await r.goto(c.base+c.review_base.replace('REVIEWID',String(result.request)));
 assert.match(await r.locator('[data-machine-review]').innerText(),/BROWSER-UNIT-NEW/);await r.locator('[name=action]').selectOption('confirm');await Promise.all([r.waitForNavigation(),r.locator('[data-catalogue-decision] button[type=submit], [data-catalogue-decision] button[name=stage]').click()]);assert.match(await r.locator('body').innerText(),/Подтверждено/);
 await p.goto(url('day'));await p.setViewportSize({width:390,height:844});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await p.screenshot({path:'/tmp/transport-whole-pool-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: full fleet grouped by type/location, outside-camp and substitute facts without moves, day cleanup, selected-only and individual movements, model locking, planning-type and machine review queues, mobile');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
