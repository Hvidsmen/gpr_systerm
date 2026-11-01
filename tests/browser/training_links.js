const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{
 const c=JSON.parse(process.env.TRAINING_LINK_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
 await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:c.base});
 await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));await page.goto(c.base+c.list);
 const button=page.getByRole('button',{name:'Скопировать ссылку для участников',exact:true}),cell=button.locator('..'),status=cell.locator('[data-training-copy-status]');
 assert.equal(await button.count(),1);assert.equal(await page.getByRole('link',{name:'Открыть копию',exact:true}).count(),0);
 await button.click();await page.waitForFunction(()=>document.querySelector('[data-training-copy-status]').textContent.includes('Ссылка скопирована'));
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),c.url);
 // Internal HTTP has no Clipboard API. Exercise the selection-copy fallback.
 await page.evaluate(async()=>{window.testClipboard=navigator.clipboard;await window.testClipboard.writeText('before-fallback');Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});});
 await button.click();await page.waitForFunction(()=>document.querySelector('[data-training-copy-status]').textContent.includes('Ссылка скопирована'));
 assert.equal(await page.evaluate(()=>window.testClipboard.readText()),c.url);
 // A browser can reject both mechanisms; the selected URL remains available.
 await page.evaluate(()=>{document.execCommand=()=>false;});await button.click();
 const input=cell.locator('[data-training-link-value]');assert(await input.isVisible());assert.equal(await input.inputValue(),c.url);
 assert(await input.evaluate(el=>document.activeElement===el&&el.selectionStart===0&&el.selectionEnd===el.value.length));
 assert((await status.innerText()).includes('вручную'));assert(await button.isEnabled());
 await page.screenshot({path:'/tmp/training-copy-link-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(400);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 await page.screenshot({path:'/tmp/training-copy-link-mobile.png',animations:'disabled'});
 await page.goto(c.base+c.edit);assert.equal(await button.getAttribute('data-training-copy-link'),c.url);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: stable participant link for non-member admin, Clipboard API, HTTP fallback, manual selection, settings page and mobile');
})().catch(error=>{console.error(error);process.exit(1);});
