const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.PERSONNEL_ASSIGNMENT_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:950}});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>route.request().url().startsWith(c.base)?route.continue():route.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.path);
  const group=()=>page.locator('[data-housing-position]').filter({has:page.locator('.housing-position-title',{hasText:c.role})});
  await group().getByRole('link',{name:'Назначить сотрудника',exact:true}).click();
  assert.equal(await page.locator('#id_assigned_from').inputValue(),c.day);
  assert((await page.locator('#id_planning_position option:checked').innerText()).includes(c.role));
  await page.locator('#id_employee + .search-select-shell button').click();
  const popup=page.locator('.search-select-popup:visible');await popup.locator('input').fill(c.name);
  await popup.getByRole('option',{name:new RegExp(c.name)}).click();
  assert.equal(await page.locator('#id_employee').inputValue(),String(c.employee));
  await page.screenshot({path:'/tmp/personnel-assignment-form.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/personnel-assignment-form-mobile.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'Назначить сотрудника',exact:true}).click();
  await page.waitForURL(url=>url.pathname.endsWith('/housing/')&&url.hash==='#roster');
  assert((await group().innerText()).includes(c.name));
  assert.equal(await group().locator('[data-position-fact-value=on_shift]').innerText(),'0');
  assert.equal(await group().locator('.housing-position-counts b').innerText(),'1');
  await page.setViewportSize({width:1440,height:950});await group().scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/personnel-assignment-roster.png',animations:'disabled'});
  await group().getByRole('link',{name:'Назначение',exact:true}).click();
  assert.equal(await page.locator('#id_employee').inputValue(),String(c.employee));
  assert(await page.locator('#id_employee + .search-select-shell button').isDisabled());
  assert.deepEqual(errors,[]);console.log('PASS: empty plan header -> employee search -> role/object/date prefilled -> mobile form -> save -> one assigned person, zero on-shift -> edit with locked employee; no page errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
