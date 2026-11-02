const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{
 const c=JSON.parse(process.env.SMG_PERIOD_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[];
 await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
 const inputPath=new URL(c.path,c.base).pathname;
 let writes=0;page.on('request',request=>{if(['POST','PATCH','PUT','DELETE'].includes(request.method())&&new URL(request.url()).pathname===inputPath)writes++;});
 const foldState=async(scope,expanded)=>{
  assert(await scope.locator('[data-report-toggle]').evaluateAll((nodes,value)=>nodes.every(el=>el.getAttribute('aria-expanded')===String(value)),expanded));
  assert(await scope.locator('[data-report-branch]').evaluateAll((nodes,value)=>nodes.every(el=>el.hidden===!value),expanded));
  assert(await scope.locator('details.production-report-section, details.production-report-days').evaluateAll((nodes,value)=>nodes.every(el=>el.open===value),expanded));
 };
 const foldAll=async(scope,expanded)=>{await scope.locator(expanded?'[data-report-expand]':'[data-report-collapse]').click();await foldState(scope,expanded);};
 await page.goto(c.base+c.path);
 const panel=page.locator('[data-smg-period]'),work=page.locator('[data-work-fact-row]').filter({has:page.locator(`[name$="-project_work"][value="${c.work}"]`)});
 assert.equal(await page.locator('.production-steps [data-step]').count(),5);
 await work.locator('[name$="-actual_quantity"]').fill('4');
 await page.locator('[data-step="5"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Обновлено'));
 assert(await panel.isVisible());assert(await page.locator('[data-entry-step="4"]').isHidden());
 const row=panel.locator('#smg-period-works tbody tr').first();
 assert.equal((await row.locator('td').nth(1).innerText()).trim(),'12');
 assert((await row.locator('td').nth(2).innerText()).startsWith('6'));
 const selectTab=async key=>{await panel.locator(`[data-period-tab="${key}"]`).click();assert.equal(await panel.locator('[data-period-panel]:visible').count(),1);assert(await panel.locator(`[data-period-panel="${key}"]`).isVisible());};
 assert.deepEqual(await panel.locator('[data-period-tab]').evaluateAll(nodes=>nodes.map(n=>n.dataset.periodTab)),['works','equipment','labor','fuel']);
 await selectTab('equipment');
 assert((await panel.locator('#smg-period-equipment').innerText()).includes('маш.·дн.'));
 assert((await panel.locator('#smg-period-equipment summary').first().innerText()).includes(c.category));
 await selectTab('labor');assert((await panel.locator('#smg-period-labor summary').first().innerText()).includes(c.group));
 await selectTab('fuel');assert((await panel.innerText()).includes('Расход, м³'));assert(await panel.locator('[data-period-days-toggle]').isHidden());assert.equal(await panel.locator('[data-report-collapse]:visible').count(),0);
 await selectTab('works');
 await panel.locator('[data-period-tab="works"]').focus();await page.keyboard.press('ArrowRight');assert(await panel.locator('[data-period-panel="equipment"]').isVisible());
 await page.keyboard.press('End');assert(await panel.locator('[data-period-panel="fuel"]').isVisible());await page.keyboard.press('Home');assert(await panel.locator('[data-period-panel="works"]').isVisible());
 const camera=panel.locator('[data-work-total]').filter({hasText:'Установка камеры'});
 assert.equal((await camera.locator('td').nth(1).innerText()).trim(),'5');assert((await camera.locator('td').nth(2).innerText()).startsWith('3'));
 const branch=camera.locator('..').locator('xpath=following-sibling::tbody[1]');assert((await branch.innerText()).includes('ПК-1'));assert((await branch.innerText()).includes('ПК-2'));
 await camera.locator('[data-report-toggle]').click();assert(await branch.isHidden());await camera.locator('[data-report-toggle]').click();assert(await branch.isVisible());
 const beforeFolding=writes;
 for(const key of ['works','equipment','labor']){
  await selectTab(key);const scope=panel.locator(`[data-period-panel="${key}"]`);
  assert.equal(await panel.locator('[data-report-collapse]:visible').count(),1);
  await foldAll(scope,false);
  for(const other of ['works','equipment','labor'].filter(value=>value!==key))await foldState(panel.locator(`[data-period-panel="${other}"]`),true);
  if(key==='works'){
   const section=scope.locator('details.production-report-section').first();await section.locator('summary').first().click();
   const toggle=section.locator('[data-report-toggle]').first();await toggle.click();assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  }
  await foldAll(scope,true);
 }
 assert.equal(writes,beforeFolding,'Folding must not submit the fact form');
 await selectTab('works');
 await panel.locator('[data-period-days-toggle]').click();await page.waitForFunction(()=>document.querySelector('[data-period-days-toggle]')?.getAttribute('aria-pressed')==='true');
 await panel.locator('[data-period-days-toggle]').hover();assert(await panel.locator('[data-period-days-toggle]').evaluate(el=>getComputedStyle(el).color!==getComputedStyle(el).backgroundColor));
 const grid=panel.locator('[data-period-panel="works"] [data-report-day-grid]');assert.equal(await panel.locator('[data-report-day-grid]:visible').count(),1);assert.equal(await grid.locator('thead').count(),1);
 const columns=await grid.locator('thead th').count();assert(columns>=31&&columns<=34);
 const geometry=()=>grid.evaluate(el=>{const rect=node=>{const r=node.getBoundingClientRect();return{x:r.x,y:r.y}};return{header:rect(el.querySelector('thead')),label:rect(el.querySelector('[data-day-label]')),metric:rect(el.querySelector('tbody .report-day-metric')),total:rect(el.querySelector('tbody .report-day-period')),totalHeader:rect(el.querySelector('thead .report-day-period')),left:el.scrollLeft,top:el.scrollTop}});
 await grid.scrollIntoViewIfNeeded();const before=await geometry();
 const labelBox=await grid.locator('thead .report-day-label').boundingBox(),metricBox=await grid.locator('thead .report-day-metric').boundingBox(),totalBox=await grid.locator('thead .report-day-period').boundingBox();assert(Math.abs(labelBox.x+labelBox.width-metricBox.x)<2);assert(Math.abs(metricBox.x+metricBox.width-totalBox.x)<2);
 const top=panel.locator('[data-period-panel="works"] [data-report-top-scroll]');assert(await top.isVisible());
 await top.evaluate(el=>el.scrollLeft=600);await page.waitForTimeout(150);assert.equal(await grid.evaluate(el=>el.scrollLeft),await top.evaluate(el=>el.scrollLeft));
 await grid.evaluate(el=>{el.scrollLeft=800;el.scrollTop=100});await page.waitForTimeout(150);const after=await geometry();
 assert.equal(await grid.evaluate(el=>el.scrollLeft),await top.evaluate(el=>el.scrollLeft));
 assert(after.left>before.left);assert(after.top>before.top);assert(Math.abs(after.label.x-before.label.x)<2);assert(Math.abs(after.metric.x-before.metric.x)<2);
 assert(Math.abs(after.total.x-before.total.x)<2);assert(Math.abs(after.totalHeader.x-before.totalHeader.x)<2);
 // Individual header cells stick; the table's thead box itself scrolls.
 assert.equal(await grid.locator('thead th').first().evaluate(el=>getComputedStyle(el).position),'sticky');
 const sticky=await grid.locator('thead th').first().boundingBox(),bounds=await grid.boundingBox();assert(Math.abs(sticky.y-bounds.y)<3);
 const stickyTotal=await grid.locator('thead .report-day-period').boundingBox();assert(Math.abs(stickyTotal.y-bounds.y)<3);
 assert.equal(await grid.locator('tbody .report-day-period').first().evaluate(el=>getComputedStyle(el).position),'sticky');
 await grid.evaluate(el=>{el.scrollLeft=0;el.scrollTop=0});
 const toggle=grid.locator('[data-report-toggle]').first(),key=await toggle.getAttribute('data-report-toggle'),children=grid.locator(`[data-report-branch="${key}"]`);
 await toggle.click();assert(await children.first().isHidden());await toggle.click();assert(await children.first().isVisible());
 await foldAll(panel.locator('[data-period-panel="works"]'),false);await foldAll(panel.locator('[data-period-panel="works"]'),true);
 await page.screenshot({path:'/tmp/smg-period-by-day-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 const mobileBefore=await geometry();
 const dateSpace=await grid.evaluate(el=>{const total=el.querySelector('thead .report-day-period').getBoundingClientRect();return el.getBoundingClientRect().left+el.clientWidth-total.right;});
 await page.screenshot({path:'/tmp/smg-sticky-total-mobile.png',animations:'disabled'});
 assert(dateSpace>=80,'Frozen columns must leave room for a date on a narrow screen: '+dateSpace);
 await grid.evaluate(el=>el.scrollLeft+=400);await page.waitForTimeout(100);const mobileAfter=await geometry();
 assert(Math.abs(mobileAfter.total.x-mobileBefore.total.x)<2);assert(Math.abs(mobileAfter.totalHeader.x-mobileBefore.totalHeader.x)<2);
 await grid.scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/smg-period-by-day-mobile.png',animations:'disabled'});
 await page.setViewportSize({width:1440,height:1000});

 assert(await panel.locator('.production-report-days').count()>0);assert((await panel.locator('.production-report-days').first().innerText()).includes('План'));
 await selectTab('equipment');assert(await panel.locator('[data-period-panel="equipment"] [data-report-day-grid]').isVisible());
 await foldAll(panel.locator('[data-period-panel="equipment"]'),false);await foldState(panel.locator('[data-period-panel="works"]'),true);await foldAll(panel.locator('[data-period-panel="equipment"]'),true);
 await selectTab('labor');await foldAll(panel.locator('[data-period-panel="labor"]'),false);await foldAll(panel.locator('[data-period-panel="labor"]'),true);await selectTab('equipment');
 await panel.locator('[data-period-refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Обновлено'));
 assert(await panel.locator('[data-period-panel="equipment"]').isVisible());
 await panel.locator('[data-period-days-toggle]').click();await page.waitForFunction(()=>document.querySelector('[data-period-days-toggle]')?.getAttribute('aria-pressed')==='false');
 assert(await panel.locator('[data-period-panel="equipment"]').isVisible());await selectTab('works');
 assert.equal(await panel.locator('.production-report-days').count(),0);
 await panel.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/smg-period-desktop.png',animations:'disabled'});
 const downloaded=page.waitForEvent('download');await panel.locator('[data-period-export]').click();const file=await downloaded;
 assert(file.suggestedFilename().endsWith('.xlsx'));assert(fs.statSync(await file.path()).size>1000);
 await page.locator('[data-step="4"]').click();assert(await page.locator('[data-entry-step="4"]').isVisible());assert(await panel.isHidden());
 await page.waitForFunction(()=>document.querySelector('[data-daily-equipment-status]').textContent.includes('обновлена'));
 assert((await page.locator('[data-report-people] summary').first().innerText()).includes(c.group));
 await page.locator('[data-production-report]').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/production-grouped-day-desktop.png',animations:'disabled'});
 const dayCamera=page.locator('[data-report-works] [data-work-total]').filter({hasText:'Установка камеры'});
 assert.equal((await dayCamera.locator('td').nth(1).innerText()).trim(),'3');
 await dayCamera.locator('[data-report-toggle]').click();assert.equal(await dayCamera.locator('[data-report-toggle]').getAttribute('aria-expanded'),'false');
 await page.locator('[data-step-next]').click();await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Обновлено'));assert(await panel.isVisible());assert(await page.locator('[data-step-next]').isHidden());
 // A failed refresh retains the last report with an explicit warning.
 await page.route('**/production/smg-period/?*',route=>route.fulfill({status:503,body:'offline'}));
 await panel.locator('[data-period-refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Не удалось'));
 assert((await panel.locator('[data-period-state]').innerText()).includes('предыдущий результат'));assert(await row.isVisible());
 await page.unroute('**/production/smg-period/?*');
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(400);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 await panel.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/smg-period-mobile.png',animations:'disabled'});
 await page.locator('[data-step-previous]').click();assert(await page.locator('[data-entry-step="4"]').isVisible());
 await page.goto(c.base+c.path+'&entry_step=5&report_section=labor');await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Обновлено'));
 assert(await page.locator('[data-smg-period]').isVisible());assert(await panel.locator('[data-period-panel="labor"]').isVisible());
 await page.goto(c.base+c.readonly_path);await page.waitForFunction(()=>document.querySelector('[data-period-state]').textContent.includes('Обновлено'));
 assert(await page.locator('[data-period-refresh]').isEnabled());
 for(const byDay of ['0','1']){
  await page.goto(c.base+c.standalone_path+'&by_day='+byDay);
  const scope=page.locator('[data-equipment-report] [data-period-panel="works"]');
  await foldAll(scope,false);await foldAll(scope,true);
  if(byDay==='1'){
   const ownGrid=scope.locator('[data-report-day-grid]');
   const total=ownGrid.locator('tbody .report-day-period').first(),before=await total.boundingBox();
   await ownGrid.evaluate(el=>el.scrollLeft=500);const after=await total.boundingBox();assert(Math.abs(after.x-before.x)<2);
  }
 }
 await page.setViewportSize({width:390,height:844});
 assert(await page.locator('[data-report-collapse]:visible').isVisible());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 await page.screenshot({path:'/tmp/planfact-fold-buttons-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS: fifth tab, save before report, period totals, Excel, failed refresh, mobile, direct link');
})().catch(error=>{console.error(error);process.exit(1);});
