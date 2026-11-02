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
 // Short owner passwords must also be accepted by the ordinary change-password form.
 await page.goto(c.base+c.password_change);
 await page.locator('[name=old_password]').fill(c.password);await page.locator('[name=new_password1]').fill('admin');await page.locator('[name=new_password2]').fill('admin');
 await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();assert((await page.innerText('body')).includes('Пароль успешно изменен.'));
 const cookies=await context.cookies();assert(cookies.some(cookie=>cookie.name.startsWith('staging_')&&cookie.name.endsWith('_session')));assert(cookies.some(cookie=>cookie.name==='sessionid'&&cookie.value===c.source_cookie));
 for(const path of c.blocked){const response=await context.request.get(c.base+path);assert.equal(response.status(),403);}
 await page.goto(c.base+c.update_path);
 assert(await page.getByRole('heading',{name:'Обновление тестового портала',exact:true}).isVisible());
 assert(await page.getByRole('button',{name:'Проверить обновления',exact:true}).isEnabled());
 assert(await page.getByRole('button',{name:'Обновить тестовый портал',exact:true}).isDisabled());
 assert.equal(await page.locator('#portal-update-notice').count(),0);
 if(c.web_release){
  await page.getByRole('button',{name:'Проверить обновления',exact:true}).click();
  await page.waitForURL(c.base+c.update_path);
  await page.waitForFunction(()=>document.querySelector('[data-test-available]').textContent==='bbbbbbbbbbbb');
  assert(await page.getByRole('button',{name:'Обновить тестовый портал',exact:true}).isEnabled());
  await page.screenshot({path:'/tmp/staging-update-before.png',animations:'disabled'});
  await page.getByRole('button',{name:'Обновить тестовый портал',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-test-current]')?.textContent==='bbbbbbbbbbbb',null,{timeout:600000});
  await page.waitForFunction(()=>document.querySelector('[data-test-status]')?.textContent.includes('обновлён и доступен'),null,{timeout:60000});
  await page.waitForFunction(()=>document.getElementById('staging-update')?.dataset.version==='b'.repeat(40));
  await page.screenshot({path:'/tmp/staging-update-after.png',animations:'disabled'});
 }else{
  await page.screenshot({path:'/tmp/staging-update-before.png',animations:'disabled'});
 }
 if(c.refresh_data){
  const refresh=page.getByRole('button',{name:'Обновить данные с основного портала',exact:true});
  assert(await refresh.isDisabled());
  await page.locator('[data-test-confirm]').check();
  assert(await refresh.isEnabled());
  await page.screenshot({path:'/tmp/staging-refresh-confirm.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/staging-refresh-mobile.png',animations:'disabled'});
  await page.setViewportSize({width:1440,height:950});
  await refresh.click();
  await page.waitForFunction(()=>{const login=document.querySelector('[data-test-login]');return location.pathname.includes('/accounts/login/') || (login && !login.hidden);},null,{timeout:300000});
  if(!page.url().includes(c.login_path))await page.getByRole('link',{name:'Войти в тестовый портал',exact:true}).click();
  await page.waitForURL(c.base+c.login_path+'**');
  await page.locator('[name=username]').fill(c.login);await page.locator('[name=password]').fill(c.password);
  await page.getByRole('button',{name:'Войти',exact:true}).click();
  await page.waitForURL(url=>!url.pathname.includes(c.login_path));
  await page.goto(c.base+c.update_path);
  assert((await page.innerText('body')).includes('Данные теста обновлены'));
  assert(!(await page.innerText('body')).includes('при создании теста'));
  assert(await page.getByRole('button',{name:'Обновить данные с основного портала',exact:true}).isDisabled());
  await page.screenshot({path:'/tmp/staging-refresh-complete.png',animations:'disabled'});
 }
 const post=async path=>{const cookie=(await context.cookies()).find(cookie=>cookie.name.startsWith('staging_')&&cookie.name.endsWith('_csrf'));return context.request.post(c.base+path,{headers:{'X-CSRFToken':cookie.value,'Referer':c.base+'/'} ,form:{}});};
 const impersonation=await post(c.impersonate);assert(impersonation.ok());await page.goto(c.base+c.settings);assert((await page.innerText('body')).includes('Вы работаете как'));assert((await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 assert.equal((await context.request.get(c.base+c.update_path)).status(),403);
 const stopped=await post(c.stop);assert(stopped.ok());await page.goto(c.base+c.settings);assert(!(await page.innerText('body')).includes('Вы работаете как'));
 await page.screenshot({path:'/tmp/staging-portal-desktop.png',animations:'disabled'});
 // Both processes use one host: visiting staging must not log the owner out of production.
 await page.goto(c.source+c.settings);assert(!page.url().includes(c.login_path));assert(!(await page.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 await page.locator('.sidebar-admin > summary').click();
 const sourceLink=page.getByRole('link',{name:'Тестовый портал',exact:true});
 assert.equal(await sourceLink.getAttribute('target'),'_blank');
 assert((await sourceLink.getAttribute('rel')).includes('noopener'));
 assert(!(await sourceLink.getAttribute('class')).split(/\s+/).includes('active'));
 await page.screenshot({path:'/tmp/staging-source-link.png',animations:'disabled'});
 const popupPromise=context.waitForEvent('page');await sourceLink.click();const popup=await popupPromise;
 await popup.waitForURL(url=>url.origin===new URL(c.base).origin);await popup.waitForLoadState();
 assert((await popup.innerText('body')).includes('ТЕСТОВЫЙ ПОРТАЛ'));
 assert.equal(await popup.getByRole('link',{name:'Тестовый портал',exact:true}).count(),0);
 assert(page.url().startsWith(c.source));await popup.close();
 await page.setViewportSize({width:390,height:844});await page.goto(c.base+c.settings);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:'/tmp/staging-portal-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: independent staging owner login with admin password, short-password change form, blocked other user, distinct cookies on same host, role impersonation, no integrations, banner and mobile');
})().catch(error=>{console.error(error);process.exit(1)});
