const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.MONTHLY_GRID_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040},permissions:['clipboard-read','clipboard-write']});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  async function cardStyle(selector){return page.locator(selector).first().evaluate(el=>{const card=getComputedStyle(el),heading=getComputedStyle(el.querySelector('summary'));return {border:card.border,padding:card.padding,radius:card.borderRadius,heading:heading.backgroundColor,font:heading.fontSize,weight:heading.fontWeight};});}
  await page.goto(c.base+c.empty_single);const singleStyle=await cardStyle('.plan-workbench-section'),singleAdd=await page.locator('.plan-workbench-add button').allTextContents();
  await page.goto(c.base+c.empty_months);
  assert.equal(await page.locator('[data-grid-section-heading]').count(),4);assert.deepEqual(await page.locator('.plan-workbench-add button').allTextContents(),singleAdd);assert.equal(singleAdd.length,6);
  assert.equal(await page.locator('[data-grid-empty-section]:visible').count(),4);
  assert.deepEqual(await cardStyle('[data-grid-section-heading]'),singleStyle,'Section cards should match the single-month editor');
  await page.screenshot({path:'/tmp/gpr-month-grid-empty-cards.png',fullPage:true});
  await page.getByRole('combobox',{name:'Состав плана',exact:true}).selectOption('labor');assert.equal(await page.locator('[data-grid-section-heading]:visible').count(),1);assert(await page.locator('[data-grid-section-heading=labor]').isVisible());
  await page.getByRole('combobox',{name:'Состав плана',exact:true}).selectOption('');
  await page.locator('[data-grid-section-heading=work]>summary').click();assert.equal(await page.locator('[data-grid-empty-section=work]').isVisible(),false);await page.locator('[data-grid-section-heading=work]>summary').click();
  await page.goto(c.base+c.url);await page.getByRole('link',{name:'По месяцам · весь период',exact:true}).click();const root=page.locator('[data-month-grid]');
  assert.equal(await root.locator('[data-grid-header-scroll] thead').count(),1);assert.equal(await root.locator('[data-grid-month]').count(),14);
  assert.deepEqual(errors,[],'monthly grid initialization');
  for(const [label,kind] of [['Должность','labor'],['Техника','equipment'],['ГСМ','fuel']]){await page.getByRole('button',{name:label,exact:true}).click();const resource=page.locator('[data-plan-add-dialog]');await resource.waitFor({state:'visible'});assert(await resource.isVisible());assert.equal(await resource.locator('[name=kind]').inputValue(),kind);assert.equal(await resource.locator('[name=months]').count(),14);await resource.getByRole('button',{name:'Отмена',exact:true}).click();}
  const original=page.url();await page.getByRole('button',{name:'Комплексная работа',exact:true}).click();const dialog=page.locator('#planning-branch-picker');await dialog.waitFor({state:'visible'});assert.equal(page.url(),original);
  await dialog.locator('[name=selection]').selectOption('package:'+c.composite);for(const checkbox of await dialog.locator('[name=months]').all())await checkbox.uncheck();
  await dialog.locator('[name=months][value="2027-02-01"]').check();await dialog.locator('[name=months][value="2027-03-01"]').check();await Promise.all([page.waitForNavigation(),dialog.getByRole('button',{name:'Добавить в план'}).click()]);
  const simple=root.locator(`[data-grid-entry][data-kind=work][data-source="${c.source}"]`),equipment=root.locator(`[data-grid-entry][data-kind=resource][data-source="${c.equipment}"]`);
  const cell=(entry,field,month)=>entry.locator(`[data-field="${field}"] [data-month="${month}"] input`);
  await cell(simple,'quantity','2026-11-01').evaluate(input=>{const data=new DataTransfer();data.setData('text/plain','8\t12,5\t9');input.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));});
  assert.equal(await cell(simple,'quantity','2026-12-01').inputValue(),'12.5');
  const complex=root.locator('[data-grid-entry][data-kind=work]').filter({has:page.locator(`[data-field="item:${c.item_a}"]`)});
  await cell(complex,'item:'+c.item_a,'2027-02-01').fill('30');await cell(complex,'item:'+c.item_b,'2027-02-01').fill('45');assert.equal(await root.locator('[data-grid-ready-source="'+c.composite+'"] [data-month="2027-02-01"] [data-grid-number]').innerText(),'15');
  await cell(equipment,'count','2026-11-01').fill('2');await cell(equipment,'count','2026-12-01').fill('3');
  await root.locator('[data-grid-section-heading=equipment]>summary').click();
  const busyRoute=async route=>route.request().method()==='POST'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'База занята другим сохранением. Введённые значения остались в форме.',retryable:true})}):route.continue();
  await page.route('**/planning/workspace-versions/*/months/',busyRoute);
  await page.locator('button[form=allocation-editor][value=save]').click();await page.waitForFunction(()=>document.querySelector('[data-grid-status]').textContent.includes('База занята'));
  assert.equal(await cell(simple,'quantity','2026-12-01').inputValue(),'12.5');assert(await cell(simple,'quantity','2026-12-01').isEnabled());
  assert.equal(await page.locator('#allocation-editor').getAttribute('data-dirty'),'true');
  await page.unroute('**/planning/workspace-versions/*/months/',busyRoute);
  await Promise.all([page.waitForNavigation(),page.locator('button[form=allocation-editor][value=save]').click()]);
  assert.equal(await root.locator('[data-grid-section-heading=equipment]').evaluate(el=>el.open),false);await root.locator('[data-grid-section-heading=equipment]>summary').click();
  assert.equal(await cell(simple,'quantity','2026-12-01').inputValue(),'12.500');assert.equal(await root.locator('[data-grid-ready-source="'+c.composite+'"] [data-month="2027-02-01"] [data-grid-number]').innerText(),'15');
  const people=root.locator('[data-grid-entry][data-section=labor]').first();assert.equal(await cell(people,'count','2026-12-01').inputValue(),'6');
  const viewport=root.locator('[data-grid-header-scroll]'),top=root.locator('[data-grid-top-scroll]');await top.evaluate(el=>el.scrollLeft=250);await page.waitForFunction(()=>[...document.querySelectorAll('[data-grid-scroll],[data-grid-header-scroll]')].filter(el=>el.getClientRects().length).every(el=>Math.abs(el.scrollLeft-250)<2));
  const alignment=await root.locator('[data-grid-header-scroll] th:nth-child(3)').evaluate(el=>el.getBoundingClientRect().left);const numericLeft=await cell(simple,'quantity','2026-11-01').evaluate(el=>el.closest('td').getBoundingClientRect().left);assert(Math.abs(alignment-numericLeft)<2,'Month columns should align with the common header');
  await root.locator('[data-grid-axis]').evaluate(el=>scrollTo(0,el.getBoundingClientRect().top+scrollY));await page.waitForFunction(()=>{const axis=document.querySelector('[data-grid-axis]'),add=document.querySelector('.plan-workbench-add');return Math.abs(axis.getBoundingClientRect().top-add.getBoundingClientRect().bottom-6)<3;});
  await root.locator('[data-grid-search]').fill('Simple');assert.equal(await root.locator('[data-grid-section-heading]:visible').count(),1);await root.locator('[data-grid-search]').fill('');
  await root.locator('[data-grid-search]').fill('missing-resource');assert(await root.locator('[data-grid-no-matches]').isVisible());await root.locator('[data-grid-search]').fill('');
  await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(100);await page.screenshot({path:'/tmp/gpr-month-grid-wide.png',fullPage:true});
  await cell(simple,'quantity','2026-11-01').fill('');await page.locator('button[form=allocation-editor][value=save]').click();assert((await root.locator('[data-grid-status]').innerText()).includes('Проверьте число'));await cell(simple,'quantity','2026-11-01').fill('8.000');
  await root.locator('[data-grid-copy]').click();assert((await page.evaluate(()=>navigator.clipboard.readText())).includes('12.500'));
  await page.setViewportSize({width:390,height:844});await page.keyboard.press('Escape');await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+2);await page.screenshot({path:'/tmp/gpr-month-grid-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: month grid across years, add branch in selected months, rectangular Excel paste, partial components, atomic save, automatic crews, matching section cards and empty states, collapse retained after save, sticky common heading, aligned synchronized scroll, filtering, invalid input, copy and mobile');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
