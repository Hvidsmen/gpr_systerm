const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.PLAN_WORKBENCH_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1720,height:1050}});
  await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:c.base});
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   if(url.endsWith('/bootstrap-icons.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap-icons.css')});return route.abort();});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.gpr,{waitUntil:'networkidle'});
  const gprAdd=await page.locator('.plan-workbench-add').innerText();
  async function assertHierarchy(){
   assert((await page.locator('[data-plan-level=section] > summary').first().innerText()).includes('Системы безопасности'));
   assert((await page.locator('[data-plan-level=object] > summary').first().innerText()).includes('УЗА 26'));
   assert((await page.locator('[data-plan-level=package] > summary').first().innerText()).includes('Монтаж системы'));
   assert.equal(await page.locator('[data-plan-level=section] [data-plan-level=object] [data-plan-level=package]').count(),1);
  }
  await assertHierarchy();
  await page.locator('[data-plan-level-select]').selectOption('package');await page.locator('[data-plan-fold-level=false]').click();
  assert(!(await page.locator('[data-plan-level=package]').evaluate(node=>node.open)));
  await page.locator('[data-plan-copy-table]').click();assert((await page.evaluate(()=>navigator.clipboard.readText())).includes('20'),'Folded works must remain in Excel copying');
  await page.reload({waitUntil:'networkidle'});assert(!(await page.locator('[data-plan-level=package]').evaluate(node=>node.open)),'Fold state should survive reload');
  await page.locator('[data-plan-level-select]').selectOption('package');await page.locator('[data-plan-fold-level=true]').click();
  assert(await page.locator('[data-plan-level=package]').evaluate(node=>node.open));

  await page.locator('.plan-extra-filters > summary').click();
  assert.equal(await page.locator('[data-plan-multi-filters] .rg-multi').count(),6);
  await page.locator('[data-plan-filter=work] summary').click();await page.locator('[data-plan-filter=work] input[type=checkbox]').first().check();
  assert.equal(await page.locator('[data-plan-filter=work] input:checked').count(),1);
  await page.locator('#smg-reset').click();assert.equal(await page.locator('[data-plan-multi-filters] input:checked').count(),0);
  await page.locator('#smg-search').fill('Нет такой позиции');assert(await page.locator('#smg-empty').isVisible());await page.locator('#smg-reset').click();
  await page.locator('.plan-extra-filters > summary').click();

  const field=id=>page.locator('[data-plan-month-input][data-item="'+id+'"]');
  assert.equal(await field(c.a).inputValue(),'20');assert.equal(await page.locator('[data-plan-month-input]').count(),2);
  await field(c.a).fill('42');
  assert.equal(await page.locator('[data-computed-source] [data-plan-number]').innerText(),'0');
  assert((await page.locator('[data-plan-save-status]').innerText()).includes('изменения'));
  async function save(){await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),page.locator('[data-plan-action-header] button[form=allocation-editor][value=save]').click()]);}
  await save();assert.equal(await field(c.a).inputValue(),'42');assert.equal(await page.locator('[data-plan-save-status]').innerText(),'Сохранено');
  await page.locator('[data-plan-settings]').first().click();assert(await page.locator('[data-plan-settings-dialog]').isVisible());await page.locator('[data-plan-apply-settings]').click();assert.equal(await page.locator('[data-plan-save-status]').innerText(),'Сохранено');
  await page.locator('[data-open-branch=work]:visible').click();
  const branch=page.locator('#planning-branch-picker');await branch.locator('[data-branch-search]').fill('Оборудование');await branch.locator('select[name=selection]').selectOption('item:'+c.b);
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),branch.locator('[data-submit-branch]').click()]);
  assert.equal(await field(c.a).inputValue(),'42');await field(c.b).fill('30');
  assert.equal(await page.locator('[data-computed-source] [data-plan-number]').innerText(),'10');
  const equipment=page.locator('[data-smg-section=equipment] [data-plan-entry]').first();
  await equipment.locator('[data-field=count]').fill('3');assert.equal(await equipment.locator('[data-field=hours]').count(),0);await save();
  const measure=field(c.b).locator('xpath=ancestor::tr');await measure.locator('[data-plan-copy-row]').click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'30');
  await page.locator('[data-plan-copy-table]').click();let copy=await page.evaluate(()=>navigator.clipboard.readText());assert(copy.includes('42')&&copy.includes('30')&&!copy.includes('Маш·ч')&&!copy.includes('Чел·ч'));
  const columns=copy.split('\r\n').map(line=>line.split('\t').length);assert(columns.every(n=>n===columns[0]),'TSV columns must align in Excel');
  await page.locator('[data-plan-add-resource=labor]').click();const add=page.locator('[data-plan-add-dialog]');
  await add.locator('select[name=brigade]').selectOption(String(c.brigade),{force:true});
  await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),add.getByRole('button',{name:'Добавить в план',exact:true}).click()]);
  assert(await page.locator('[data-smg-section=labor] [data-plan-month-input]').count());
  assert.equal(await page.locator('[data-smg-section=equipment] > [data-plan-section-totals]').count(),1);
  assert(!(await page.locator('[data-smg-section=equipment] > [data-plan-section-totals]').evaluate(node=>Boolean(node.previousElementSibling&&node.previousElementSibling.hasAttribute('data-smg-group')))));
  await page.screenshot({path:'/tmp/unified-gpr-wide.png',fullPage:false});
  await page.goto(c.base+c.report,{waitUntil:'networkidle'});
  const resourceTotal=page.locator('[data-resource-section-total=equipment]');assert.equal(await resourceTotal.count(),1);
  assert(await resourceTotal.evaluate(node=>node.previousElementSibling?.tagName==='SUMMARY'),'Period total should be directly below the section heading');
  assert.equal(await page.locator('summary').filter({hasText:'Итого: Техника'}).count(),0);
  assert((await page.locator('label[for=id_labor_q]').textContent()).includes('Должность'));
  assert((await page.locator('label[for=id_brigade_group]').textContent()).includes('Группа должностей'));
  await resourceTotal.scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/planning-report-header-totals.png'});

  await page.goto(c.base+c.smg,{waitUntil:'networkidle'});await assertHierarchy();assert.equal(await page.locator('.plan-workbench-add').innerText(),gprAdd);
  assert.equal(await page.locator('[data-plan-month-input]').count(),0);assert.equal(await page.locator('.smg-day-input').count(),31);
  assert.equal(await page.locator('[data-workbench-year]').count(),1);
  await page.locator('[data-plan-copy-table]').click();copy=await page.evaluate(()=>navigator.clipboard.readText());assert.equal(copy.split('\r\n')[0].split('\t').length,34);
  await page.screenshot({path:'/tmp/unified-smg-wide.png',fullPage:false});
  for(const url of [c.gpr,c.smg]){
   await page.goto(c.base+url,{waitUntil:'networkidle'});await page.setViewportSize({width:390,height:844});
   await page.waitForFunction(()=>document.getElementById('sidebar').getBoundingClientRect().right<=1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'Common editor overflows mobile viewport');
   await page.screenshot({path:url===c.gpr?'/tmp/unified-gpr-mobile.png':'/tmp/unified-smg-mobile.png'});
  }
  await context.addCookies([{name:'sessionid',value:c.manager_cookie,url:c.base}]);await page.goto(c.base+c.gpr,{waitUntil:'networkidle'});
  assert.equal(await page.locator('[data-smg-section=work] [data-plan-month-input]').count(),0);
  assert(await page.locator('[data-smg-section=equipment] [data-plan-month-input]').count());
  assert.deepEqual(errors,[]);console.log('Unified workbench browser: shared controls, partial complex edits, save/reload, scoped work addition, resource edits, row/table TSV copying, manager rights and mobile passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
