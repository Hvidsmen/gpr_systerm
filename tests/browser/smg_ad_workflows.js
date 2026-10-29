const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const c=JSON.parse(process.env.SMG_AD_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const context=await browser.newContext({viewport:{width:1500,height:1000},locale:'ru-RU'});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 async function click(button){const [response]=await Promise.all([page.waitForNavigation(),button.click()]);assert(response.status()<400);assert.equal(await page.locator('.errorlist').count(),0);}
 await page.goto(c.base+c.list);await click(page.getByRole('link',{name:'Изменить название',exact:true}));
 await page.locator('[name=name]').fill('Переименованный');await click(page.getByRole('button',{name:'Сохранить название',exact:true}));
 assert((await page.locator('body').innerText()).includes('Переименованный'));
 await click(page.getByRole('link',{name:'Копировать вариант',exact:true}));
 await page.locator('[name=month]').fill('2026-02');await page.locator('[name=name]').fill('Февральский');
 await page.screenshot({path:'/tmp/smg-copy-month-name.png',fullPage:true});await click(page.getByRole('button',{name:'Создать копию',exact:true}));
 assert((await page.locator('h1').innerText()).includes('Февральский'));assert((await page.locator('h1').innerText()).includes('02.2026'));
 await page.goto(c.base+c.ad);await page.locator('[name=access_group]').selectOption(String(c.group),{force:true});
 assert.equal(await page.locator('[name=role]').inputValue(),String(c.role));assert(await page.locator('[name=role]').isDisabled());assert(await page.locator('[name=objects]').isDisabled());
 assert.deepEqual(await page.locator('[name=objects]').evaluate(select=>[...select.selectedOptions].map(o=>o.value)),[String(c.object)]);
 assert((await page.locator('[data-access-scope]').innerText()).includes('доступа к объектам нет'));
 await page.screenshot({path:'/tmp/ad-group-inheritance.png',fullPage:true});await click(page.getByRole('button',{name:'Добавить в ГПР',exact:true}));
 assert.deepEqual(errors,[]);console.log('PASS: SMG rename, copy with target month/name, AD group first with role/object inheritance and provisioning');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
