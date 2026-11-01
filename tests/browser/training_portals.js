const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{
 const c=JSON.parse(process.env.TRAINING_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
 const training=c.base.replace(/:\d+$/,':'+c.port);
 const otherTraining=c.base.replace(/:\d+$/,':'+c.other_port);
 await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 const routeHandler=route=>{const url=route.request().url();if(url.startsWith(c.base)||url.startsWith(training)||url.startsWith(otherTraining))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();};
 await context.route('**/*',routeHandler);
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
 await page.goto(c.base+c.tasks);assert((await page.locator('main').innerText()).includes('Учебная копия'));
 await page.goto(c.base+c.entry);assert((await page.locator('h1').innerText()).includes('Учебная копия'));
 await page.getByRole('button',{name:'Войти в учебную копию'}).click();
 await page.waitForURL(training+'/**');await page.locator('[data-training-banner]').waitFor();
 assert((await page.locator('[data-training-banner]').innerText()).includes('07:00'));
 await page.goto(training+c.create);await page.locator('[name=name]').fill('Учебный проект');
 await page.locator('form button[type=submit],form input[type=submit]').first().click();
 await page.goto(training+c.projects);assert((await page.locator('main').innerText()).includes('Учебный проект'));
 await page.screenshot({path:'/tmp/training-portal-desktop.png',animations:'disabled'});
 // A second member sees the same exercises through an independent entry ticket.
 const peer=await browser.newContext();await peer.route('**/*',routeHandler);await peer.addCookies([{name:'sessionid',value:c.peer_cookie,url:c.base}]);
 const peerPage=await peer.newPage();await peerPage.goto(c.base+c.entry);await peerPage.getByRole('button',{name:'Войти в учебную копию'}).click();await peerPage.waitForURL(training+'/**');
 await peerPage.goto(training+c.projects);assert((await peerPage.locator('main').innerText()).includes('Учебный проект'));await peer.close();
 // Production cookies alone cannot authenticate against the training database.
 const guest=await browser.newContext();await guest.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 assert.equal((await guest.request.get(training+c.projects)).status(),403);await guest.close();
 // A different group has a different database and a different session cookie.
 const groupTwo=await context.newPage();await groupTwo.goto(c.base+c.other_entry);await groupTwo.getByRole('button',{name:'Войти в учебную копию'}).click();await groupTwo.waitForURL(otherTraining+'/**');
 await groupTwo.goto(otherTraining+c.projects);assert(!(await groupTwo.locator('main').innerText()).includes('Учебный проект'));await groupTwo.close();
 const cookies=await context.cookies(training);assert(cookies.some(x=>x.name.startsWith('training_')&&x.name.endsWith('_session')));
 const update=await context.request.get(training+'/accounts/admin/update/');assert.equal(update.status(),403);
 const email=await context.request.get(training+'/accounts/users/invitations/settings/');assert.equal(email.status(),403);
 const onec=await context.request.get(training+'/accounts/integrations/1c/');assert.equal(onec.status(),403);
 const login=await context.request.get(training+'/accounts/login/');assert.equal(login.status(),403);
 const main=await context.request.get(c.base+c.projects);assert(!((await main.text()).includes('Учебный проект')));
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(400);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 await page.screenshot({path:'/tmp/training-portal-mobile.png',animations:'disabled'});
 const admin=await browser.newContext({viewport:{width:1440,height:1000}});await admin.route('**/*',routeHandler);await admin.addCookies([{name:'sessionid',value:c.admin_cookie,url:c.base}]);
 const adminPage=await admin.newPage();await adminPage.goto(c.base+c.admin_path);assert.equal(await adminPage.locator('[name=members]:checked').count(),2);
 await adminPage.screenshot({path:'/tmp/training-admin-desktop.png',animations:'disabled'});await adminPage.setViewportSize({width:390,height:844});await adminPage.waitForTimeout(400);
 assert(await adminPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await adminPage.screenshot({path:'/tmp/training-admin-mobile.png',animations:'disabled'});await admin.close();
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: production entry, password-free ticket handoff, isolated admin, real project write, cookie isolation, blocked integrations, mobile');
})().catch(error=>{console.error(error);process.exit(1);});
