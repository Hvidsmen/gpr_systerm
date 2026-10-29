const {chromium} = require('playwright');
const assert = require('node:assert/strict'), fs = require('node:fs');
(async () => {
 const c=JSON.parse(process.env.USER_ADMIN_CONFIG), browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1050},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  async function visit(url){const response=await page.goto(c.base+url);assert.equal(response.status(),200);}
  await visit(c.users);assert(await page.getByRole('heading',{name:'Пользователи и доступ'}).isVisible());
  await page.screenshot({path:'/tmp/user-admin-list.png',fullPage:true});
  await visit(c.ad);
  await page.locator('[name=role]').selectOption(String(c.planner_role),{force:true});
  await page.locator('[name=objects]').selectOption([String(c.object)],{force:true});
  await page.locator('[name=access_group]').selectOption(String(c.group),{force:true});
  assert(await page.locator('[name=role]').isDisabled());assert(await page.locator('[name=objects]').isDisabled());
  assert.equal(await page.locator('[data-summary-role]').innerText(),'Склад ГСМ');
  assert((await page.locator('[data-access-scope]').innerText()).includes('Работы, люди и техника недоступны'));
  await page.locator('[name=access_group]').selectOption('',{force:true});
  assert(!(await page.locator('[name=role]').isDisabled()));
  assert.equal(await page.locator('[name=role]').inputValue(),String(c.planner_role));
  assert.deepEqual(await page.locator('[name=objects]').evaluate(el=>Array.from(el.selectedOptions).map(option=>option.value)),[String(c.object)]);
  await page.locator('[name=access_group]').selectOption(String(c.group),{force:true});
  await page.screenshot({path:'/tmp/user-admin-ad.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'AD form overflows mobile');
  await page.screenshot({path:'/tmp/user-admin-ad-mobile.png',fullPage:true});
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Добавить в ГПР',exact:true}).click()]);
  assert.equal(await page.locator('[data-summary-role]').innerText(),'Склад ГСМ');
  await visit(c.local);assert(await page.getByRole('heading',{name:'Новый локальный пользователь'}).isVisible());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Local form overflows mobile');
  await page.setViewportSize({width:1440,height:1050});await visit(c.edit);
  await page.locator('[name=access_group]').selectOption(String(c.group),{force:true});
  assert.equal(await page.locator('[name=assigned_objects]').evaluate(el=>el.selectedOptions.length),1);
  await page.screenshot({path:'/tmp/user-admin-edit.png',fullPage:true});
  for (const [index,url] of c.smg.entries()) {
   await visit(url);
   await page.locator('[data-plan-matrix] details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));
   await page.waitForFunction(()=>Array.from(document.querySelectorAll('.smg-calendar-summary,.smg-calendar-grid .matrix-scroll')).some(node=>parseFloat(node.style.getPropertyValue('--smg-inset'))>60));
   const result=await page.locator('[data-plan-matrix]').evaluate(matrix=>{
    const nodes=Array.from(matrix.querySelectorAll('.matrix-scroll,.smg-calendar-summary')).filter(node=>node.getClientRects().length);
    return nodes.map(node=>({inset:parseFloat(node.style.getPropertyValue('--smg-inset')),clip:getComputedStyle(node).clipPath,kind:node.className}));
   });
   assert(result.filter(node=>node.inset>60).length>=2,'Fixture must cover deep hierarchy');
   for(const node of result)assert.notEqual(node.clip,'none','Tree gutter is covered by '+node.kind);
   const packageNode=page.locator('.plan-hierarchy-package').first();
   const total=packageNode.locator(':scope > summary [data-package-month]');
   assert.equal(Number(await total.getAttribute('data-value')),2);
   assert.equal(Number(await packageNode.locator(':scope > summary [data-package-day="2026-01-05"]').getAttribute('data-value')),2);
   assert(!(await packageNode.locator('[data-plan-completion-row]').first().isVisible()),'Ready total duplicates below the heading');
   assert(await packageNode.locator(':scope > summary [data-package-action]').count()>=2,'Package controls must be in its heading');
   await packageNode.locator(':scope > summary').click();
   assert(await total.isVisible(),'Collapsed complex must retain its total');
   await packageNode.locator(':scope > summary').click();
   const firstDay=page.locator('[data-smg-calendar-date="2026-01-01"]');
   const expectedX=(await firstDay.boundingBox()).x;
   const actualX=(await packageNode.locator(':scope > summary [data-package-day="2026-01-01"]').boundingBox()).x;
   assert(Math.abs(actualX-expectedX)<=2,'Package totals diverge from the day columns');
   await page.locator('.plan-hierarchy-package').first().scrollIntoViewIfNeeded();
   await page.locator('[data-smg-calendar-scroll]').evaluate(node=>node.scrollLeft=0);
   await page.locator('[data-smg-calendar-header-scroll]').evaluate(node=>node.scrollLeft=0);
   await page.waitForFunction(()=>Math.abs(document.querySelector('[data-smg-calendar-scroll]').scrollLeft)<1);
   const measureX=(await packageNode.locator(':scope > summary .smg-summary-measure').boundingBox()).x;
   const headingX=(await page.locator('.smg-calendar-header-table th').nth(1).boundingBox()).x;
   assert(Math.abs(measureX-headingX)<=2,'Package measure must align with the shared heading');
   await page.screenshot({path:'/tmp/smg-guides-'+(index?'review':'edit')+'.png',animations:'disabled'});
   const gutter=await page.locator('.plan-hierarchy-package > .matrix-scroll').first().evaluate(node=>{
    const bounds=node.getBoundingClientRect(),inset=parseFloat(node.style.getPropertyValue('--smg-inset'));
    return document.elementsFromPoint(bounds.left+inset-8,bounds.top+20).includes(node);
   });
   assert(!gutter,'Table paint or hit area covers an ancestor guide');
   await page.locator('[data-smg-calendar-header-scroll]').evaluate(node=>node.scrollLeft=600);
   await page.waitForFunction(()=>document.querySelector('[data-smg-calendar-scroll]').scrollLeft===600);
   await page.locator('[data-smg-calendar-header-scroll]').evaluate(node=>node.scrollLeft=0);
   await page.setViewportSize({width:900,height:900});
   await page.locator('.plan-hierarchy-package > summary').first().click();
   await page.locator('.plan-hierarchy-package > summary').first().click();
   await page.setViewportSize({width:1440,height:1050});
  }
  assert.deepEqual(errors,[]);console.log('PASS: admin AD/local access preview, group inheritance and restore, mobile layout, AD provisioning, SMG edit and review tree guides with resize/scroll/collapse');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
