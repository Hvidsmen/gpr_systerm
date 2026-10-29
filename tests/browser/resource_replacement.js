const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const c=JSON.parse(process.env.RESOURCE_REPLACE_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1500,height:1050},locale:'ru-RU'});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const dialog=()=>page.locator('[data-resource-replace-dialog]');
  async function visit(url){const response=await page.goto(c.base+url);assert.equal(response.status(),200);await page.locator('[data-plan-matrix] details,[data-grid-root] details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));}
  async function replace(button,target,screenshot){
   await button.click();await dialog().locator('[name=target]').waitFor({state:'attached'});
   await dialog().locator('[name=target]').selectOption(String(target),{force:true});
   await dialog().getByRole('button',{name:'Показать изменения',exact:true}).click();
   await dialog().getByRole('button',{name:'Заменить в плане',exact:true}).waitFor();
   if(screenshot)await page.screenshot({path:screenshot,fullPage:true});
   const response=page.waitForResponse(r=>r.url().includes('/replace/')&&r.request().method()==='POST');
   const navigation=page.waitForNavigation();await dialog().getByRole('button',{name:'Заменить в плане',exact:true}).click();
   assert((await response).ok());await navigation;
  }
  function row(label){return page.locator('[data-plan-entry]').filter({has:page.locator('.matrix-label strong',{hasText:label})});}
  await visit(c.gpr);
  await replace(row(c.oldLabel).getByRole('button',{name:'Заменить',exact:true}),c.target,'/tmp/resource-replace-gpr-preview.png');
  assert.equal(await row(c.targetLabel).locator('[data-plan-month-input]').inputValue(),'2');
  assert((await row(c.targetLabel).innerText()).includes('Смен в день: 2'));
  await replace(row(c.laborLabel).getByRole('button',{name:'Заменить',exact:true}),c.newLabor);
  await visit(c.months);
  await page.locator('[data-grid-entry]').filter({hasText:c.targetLabel}).getByRole('button',{name:'Заменить',exact:true}).click();
  await dialog().locator('[name=target]').selectOption(String(c.empty),{force:true});
  await dialog().getByRole('button',{name:'Показать изменения',exact:true}).click();
  await dialog().getByRole('button',{name:'Заменить в плане',exact:true}).waitFor();
  assert((await dialog().innerText()).includes('не задан экипаж'));
  await Promise.all([page.waitForNavigation(),dialog().getByRole('button',{name:'Заменить в плане',exact:true}).click()]);
  await visit(c.smg);
  await replace(row(c.oldLabel).getByRole('button',{name:'Заменить',exact:true}),c.target,'/tmp/resource-replace-smg-preview.png');
  assert.equal(await row(c.targetLabel).locator('.smg-day-input').nth(0).inputValue(),'2');
  assert.equal(await row(c.targetLabel).locator('.smg-day-input').nth(1).inputValue(),'3');
  await replace(row(c.laborLabel).getByRole('button',{name:'Заменить',exact:true}),c.newLabor);
  await page.setViewportSize({width:390,height:844});
  await row(c.targetLabel).getByRole('button',{name:'Заменить',exact:true}).click();await dialog().locator('[name=target]').waitFor({state:'attached'});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/resource-replace-mobile.png',fullPage:true});
  await dialog().getByRole('button',{name:'Отмена',exact:true}).click();
  await page.setViewportSize({width:1500,height:1050});
  await context.addCookies([{name:'sessionid',value:c.estimatorCookie,url:c.base}]);
  await visit(c.review);
  assert.equal(await page.locator('[data-review-level=section]').count(),1);
  for(const id of c.facilities)assert.equal(await page.locator('[data-review-object-id="'+id+'"] [data-review-level=package]').count(),1);
  await page.locator('[data-review-expand=none]').click();assert(!(await page.locator('[data-review-level=section]').evaluate(el=>el.open)));
  await page.locator('#review-search').fill('УЗА 32');
  assert(await page.locator('[data-review-object-id="'+c.facilities[0]+'"]').isVisible());
  assert(!(await page.locator('[data-review-object-id="'+c.facilities[1]+'"]').isVisible()));
  await page.locator('[data-review-object-id="'+c.facilities[0]+'"] > summary [data-review-group-select]').check();
  assert.equal(await page.locator('.review-select:checked').count(),1);assert(await page.locator('#review-bulk').isVisible());
  await page.locator('#review-search').fill('несуществующая работа');assert(await page.locator('#review-empty').isVisible());
  await page.locator('#review-search').fill('Прокладка кабеля 33');
  assert(await page.locator('[data-review-object-id="'+c.facilities[1]+'"]').isVisible());
  assert(await page.locator('[data-review-object-id="'+c.facilities[1]+'"] [data-review-level=package]').evaluate(node=>node.open),'Search by a component work must reveal its package');
  await page.locator('#review-reset').click();await page.locator('[data-review-expand=all]').click();
  await page.screenshot({path:'/tmp/estimate-review-hierarchy.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:'/tmp/estimate-review-hierarchy-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: GPR/SMG equipment and position replacements, crew preview, counts/shifts/links retained, all-month view, mobile, estimator object hierarchy/search/group selection');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
