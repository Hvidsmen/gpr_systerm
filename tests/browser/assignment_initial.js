const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.PERSONNEL_INITIAL_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',r=>r.request().url().startsWith(c.base)?r.continue():r.abort());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(c.base+c.path);
  const group=()=>page.locator('[data-housing-position]').filter({has:page.locator('.housing-position-title',{hasText:c.role})});
  const popup=()=>page.locator('.search-select-popup:visible');
  async function chooseEmployee(name){await page.locator('#id_employee + .search-select-shell button').click();await popup().locator('input').fill(name);await popup().getByRole('option',{name:new RegExp(name)}).click();}
  async function chooseState(label){await page.locator('#id_initial-state + .search-select-shell button').click();await popup().getByRole('option',{name:label,exact:true}).click();}
  const arrival=()=>page.locator('[data-initial-arrival]'),departure=()=>page.locator('[data-initial-departure]');
  await group().getByRole('link',{name:'Назначить сотрудника',exact:true}).click();await chooseEmployee(c.mv_name);await chooseState('На вахте');
  assert(await arrival().isVisible());assert(await arrival().evaluate(e=>e.required));assert(!(await departure().isVisible()));
  await arrival().fill(c.arrival);await page.screenshot({path:'/tmp/assignment-initial-mv.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'Назначить сотрудника',exact:true}).click();await page.waitForURL(u=>u.pathname.endsWith('/housing/')&&u.hash==='#roster');
  const mv=()=>group().locator('tr[data-housing-person]').filter({hasText:c.mv_name});
  assert.equal(await mv().locator('[data-housing-state-days]').getAttribute('data-housing-state-days'),'21');
  assert.equal(await group().locator('[data-position-fact-value=on_shift]').innerText(),'1');
  await mv().getByRole('link',{name:'Назначение',exact:true}).click();assert.equal(await page.locator('[data-assignment-initial]').count(),0);
  await page.goto(c.base+c.path);await group().getByRole('link',{name:'Назначить сотрудника',exact:true}).click();
  await chooseEmployee(c.existing_name);assert(await page.locator('#id_initial-state + .search-select-shell button').isDisabled());assert(await page.locator('[data-initial-existing]').isVisible());
  await chooseEmployee(c.mo_name);await chooseState('МО — межвахтовый отдых');assert(!(await arrival().evaluate(e=>e.required)));assert(await departure().evaluate(e=>e.required));
  await departure().fill(c.departure);await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/assignment-initial-mo-mobile.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'Назначить сотрудника',exact:true}).click();await page.waitForURL(u=>u.pathname.endsWith('/housing/')&&u.hash==='#roster');
  const mo=group().locator('tr[data-housing-person]').filter({hasText:c.mo_name});
  assert.equal(await mo.locator('[data-housing-state-days]').getAttribute('data-housing-state-days'),'11');assert.equal(await mo.locator('[data-label="Фактический заезд"]').innerText(),'—');
  assert.equal(await group().locator('[data-position-fact-value=on_shift]').innerText(),'1');assert.equal(await group().locator('[data-position-fact-value=rest]').innerText(),'1');
  await page.setViewportSize({width:1440,height:1000});await group().scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/assignment-initial-roster.png',animations:'disabled'});
  assert.deepEqual(errors,[]);console.log('PASS: initial MV with arrival and 21-day count; MO without invented arrival and 11-day rest; known-history employee blocked; edit hides initialized state; desktop/mobile; no page errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
