const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.POSITION_INLINE_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(c.base+c.url);
  await page.locator('[data-step="2"]').click();
  const role=()=>page.locator(`[data-position-plan="${c.role}"]`);
  await role().getByRole('button',{name:'Назначить сотрудника'}).click();
  const person=()=>role().locator('[data-production-person]').first();
  assert.equal(await person().locator('[name$="-planning_position"]').inputValue(),String(c.role));
  await person().locator('[name$="-employee"]').selectOption(String(c.employee));
  assert.equal(await role().locator('[data-position-assigned]').textContent(),'1');
  assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'1');
  await Promise.all([page.waitForNavigation(),person().getByRole('button',{name:'Добавить в факт'}).click()]);
  assert.equal(new URL(page.url()).searchParams.get('entry_step'),'2');
  assert.equal(await page.locator('[data-step="2"]').getAttribute('aria-current'),'step');
  assert(await person().locator('[name$="-id"]').inputValue());
  assert.equal(await role().locator('[data-position-assigned]').textContent(),'1');
  assert.equal(await role().locator('[data-position-remaining]').textContent(),'1');
  assert((await page.locator('body').textContent()).includes('Черновик факта сохранён'));
  await page.screenshot({path:'/tmp/production-position-inline-wide.png',fullPage:true});
  await page.locator('[data-step="3"]').click();
  const card=page.locator('.graph-person').filter({hasText:c.role_name});
  assert((await card.locator('[data-graph-person-position]').textContent()).includes(c.role_name));
  assert((await card.locator('[data-graph-staff-position]').textContent()).includes(c.staff_role));
  await page.screenshot({path:'/tmp/production-graph-person-position.png',fullPage:true});
  await page.locator('[data-step="2"]').click();await person().locator('[data-take]').click();
  await person().locator('[data-label]').filter({hasText:'Вы заполняете'}).waitFor();
  await person().locator('[name$="-DELETE"]').check();assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'0');
  await person().locator('[name$="-DELETE"]').uncheck();assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'1');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/production-position-inline-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: employee form under selected role, one-click audited draft save, role totals, graph positions and mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
