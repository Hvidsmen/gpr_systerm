const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs');
(async()=>{
 const c=JSON.parse(process.env.STAGING_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),context=await browser.newContext({viewport:{width:1440,height:950}});
 await context.addCookies([{name:'sessionid',value:c.source_cookie,url:c.source}]);
 await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base)||url.startsWith(c.source))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(c.base+c.login_path);assert((await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));assert.equal(await page.getByRole('link',{name:'Зарегистрироваться'}).count(),0);
 await page.locator('[name=username]').fill(c.other);await page.locator('[name=password]').fill(c.other_password);await page.getByRole('button',{name:'Войти',exact:true}).click();assert(page.url().includes(c.login_path));
 await page.locator('[name=username]').fill(c.login);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'Войти',exact:true}).click();await page.waitForURL(url=>!url.pathname.includes(c.login_path));
 assert((await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 const cookies=await context.cookies();assert(cookies.some(cookie=>cookie.name.startsWith('staging_')&&cookie.name.endsWith('_session')));assert(cookies.some(cookie=>cookie.name==='sessionid'&&cookie.value===c.source_cookie));
 for(const path of c.blocked){const response=await context.request.get(c.base+path);assert.equal(response.status(),403);}
 const post=async path=>{const cookie=(await context.cookies()).find(cookie=>cookie.name.startsWith('staging_')&&cookie.name.endsWith('_csrf'));return context.request.post(c.base+path,{headers:{'X-CSRFToken':cookie.value,'Referer':c.base+'/'} ,form:{}});};
 const impersonation=await post(c.impersonate);assert(impersonation.ok());await page.goto(c.base+c.settings);assert((await page.innerText('body')).includes('Вы работаете как'));assert((await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 const stopped=await post(c.stop);assert(stopped.ok());await page.goto(c.base+c.settings);assert(!(await page.innerText('body')).includes('Вы работаете как'));
 await page.screenshot({path:'/tmp/staging-portal-desktop.png',animations:'disabled'});
 // Both processes use one host: visiting staging must not log the owner out of production.
 await page.goto(c.source+c.settings);assert(!page.url().includes(c.login_path));assert(!(await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 await page.setViewportSize({width:390,height:844});await page.goto(c.base+c.settings);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:'/tmp/staging-portal-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: independent staging owner login, blocked other user, distinct cookies on same host, role impersonation, no integrations, banner and mobile');
})().catch(error=>{console.error(error);process.exit(1)});
