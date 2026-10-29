const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.MONTHLY_GRID_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040},permissions:['clipboard-read','clipboard-write']});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url);await page.getByRole('link',{name:'По месяцам · весь период',exact:true}).click();const root=page.locator('[data-month-grid]');
  assert.equal(await root.locator('thead').count(),1);assert.equal(await root.locator('[data-grid-month]').count(),14);
  const original=page.url();await page.getByRole('button',{name:'Добавить работы',exact:true}).click();const dialog=page.locator('[data-grid-dialog=work]');await dialog.waitFor({state:'visible'});assert.equal(page.url(),original);
  await dialog.locator('[name=selection]').selectOption('package:'+c.composite);for(const checkbox of await dialog.locator('[name=months]').all())await checkbox.uncheck();
  await dialog.locator('[name=months][value="2027-02-01"]').check();await dialog.locator('[name=months][value="2027-03-01"]').check();await Promise.all([page.waitForNavigation(),dialog.getByRole('button',{name:'Добавить в план'}).click()]);
  const simple=root.locator(`[data-grid-entry][data-kind=work][data-source="${c.source}"]`),equipment=root.locator(`[data-grid-entry][data-kind=resource][data-source="${c.equipment}"]`);
  const cell=(entry,field,month)=>entry.locator(`[data-field="${field}"] [data-month="${month}"] input`);
  await cell(simple,'quantity','2026-11-01').evaluate(input=>{const data=new DataTransfer();data.setData('text/plain','8\t12,5\t9');input.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));});
  assert.equal(await cell(simple,'quantity','2026-12-01').inputValue(),'12.5');
  const complex=root.locator('[data-grid-entry][data-kind=work]').filter({has:page.locator(`[data-field="item:${c.item_a}"]`)});
  await cell(complex,'item:'+c.item_a,'2027-02-01').fill('30');await cell(complex,'item:'+c.item_b,'2027-02-01').fill('45');assert.equal(await complex.locator('[data-field=ready] [data-month="2027-02-01"] [data-grid-number]').innerText(),'15');
  await cell(equipment,'count','2026-11-01').fill('2');await cell(equipment,'count','2026-12-01').fill('3');
  await Promise.all([page.waitForNavigation(),page.locator('button[form=allocation-editor][value=save]').click()]);
  assert.equal(await cell(simple,'quantity','2026-12-01').inputValue(),'12.500');assert.equal(await complex.locator('[data-field=ready] [data-month="2027-02-01"] [data-grid-number]').innerText(),'15');
  const people=root.locator('[data-grid-entry][data-section=labor]').first();assert.equal(await cell(people,'count','2026-12-01').inputValue(),'6');
  const viewport=root.locator('[data-grid-scroll]'),top=root.locator('[data-grid-top-scroll]');await top.evaluate(el=>el.scrollLeft=250);await page.waitForFunction(()=>Math.abs(document.querySelector('[data-grid-scroll]').scrollLeft-250)<2);
  await viewport.evaluate(el=>el.scrollTop=140);await page.waitForFunction(()=>Math.abs(document.querySelector('.month-grid-table thead th').getBoundingClientRect().top-document.querySelector('[data-grid-scroll]').getBoundingClientRect().top)<3);
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'/tmp/gpr-month-grid-wide.png',fullPage:true});
  await cell(simple,'quantity','2026-11-01').fill('');await page.locator('button[form=allocation-editor][value=save]').click();assert((await root.locator('[data-grid-status]').innerText()).includes('Проверьте число'));await cell(simple,'quantity','2026-11-01').fill('8.000');
  await root.locator('[data-grid-copy]').click();assert((await page.evaluate(()=>navigator.clipboard.readText())).includes('12.500'));
  await page.setViewportSize({width:390,height:844});await page.keyboard.press('Escape');await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+2);await page.screenshot({path:'/tmp/gpr-month-grid-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: month grid across years, add branch in selected months, rectangular Excel paste, partial components, atomic save, automatic crews, sticky heading, top scroll, invalid input, copy and mobile');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
