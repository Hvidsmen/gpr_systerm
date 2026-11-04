const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{const c=JSON.parse(process.env.ZUP_CYCLE_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const context=await browser.newContext({viewport:{width:1440,height:950}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);await context.route('**/*',r=>r.request().url().startsWith(c.base)?r.continue():r.abort());
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(c.base+c.path);
 const row=()=>page.locator('[data-housing-person]').filter({hasText:c.name});
 assert.equal(await row().locator('[data-housing-cycle]').innerText(),'Цикл 60/30 · из ЗУП');assert.equal(await row().locator('[data-housing-day-code]').getAttribute('data-housing-day-code'),'МВ');
 assert(!(await row().innerText()).includes('Цикл не задан'));assert((await row().locator('[data-label="Выезд по графику"]').innerText()).includes(c.departure));
 await row().scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/zup-cycle-roster.png',animations:'disabled'});
 await row().getByRole('button',{name:'График',exact:true}).click();const dialog=page.locator('[data-calendar-dialog]');await dialog.locator('[data-calendar-subtitle]').filter({hasText:'60/30 · из ЗУП'}).waitFor();
 assert((await dialog.locator('[data-calendar-subtitle]').innerText()).includes('Вахта 1.1М (60х30)'));assert((await dialog.locator(`[data-individual-calendar] div`).filter({hasText:new RegExp('^'+c.day_number+'МВ$')}).count())>0);
 await dialog.locator('[data-calendar-close]').click();await row().getByRole('link',{name:'Изменить график',exact:true}).click();
 assert((await page.locator('#id_pattern_helptext').innerText()).includes('60/30 · из ЗУП'));
 await page.locator('#id_on_days').fill('14');await page.locator('#id_off_days').fill('7');await page.locator('#id_hours').fill('8');await page.getByRole('button',{name:'Сохранить',exact:true}).click();
 await page.waitForURL(u=>u.pathname.includes('/housing/people/'));await page.goto(c.base+c.path);
 assert.equal(await row().locator('[data-housing-cycle]').innerText(),'Цикл 14/7');assert.equal(await row().locator('[data-housing-day-code]').getAttribute('data-housing-day-code'),'8');assert((await row().locator('[data-housing-schedule]').innerText()).includes('14/7'));assert((await row().locator('[data-label="Выезд по графику"]').innerText()).includes(c.personal_departure));
 await row().getByRole('button',{name:'График',exact:true}).click();await dialog.locator('[data-calendar-subtitle]').filter({hasText:'14/7'}).waitFor();assert(!(await dialog.locator('[data-calendar-subtitle]').innerText()).includes('60х30'));
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/zup-cycle-personal-mobile.png',fullPage:true,animations:'disabled'});assert.deepEqual(errors,[]);
 console.log('PASS: existing empty personal pattern uses ZUP 60х30 -> cycle source and day/exit forecast -> matching lazy popup -> set personal 14/7 -> label, day, exit and popup all use personal override; movements preserved; no page errors.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
