const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.POSITION_INLINE_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1530,height:1040}});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',async response=>{if(response.status()>=500)console.error('Server failure:',response.status(),response.url(),(await response.text()).slice(0,120));});await page.goto(c.base+c.url);
  const saved=async()=>{assert(await page.evaluate(()=>window.productionAutoSave.flush()),await page.locator('[data-autosave-state]').textContent());};
  await page.locator('[data-step="2"]').click();const initialUrl=page.url();
  assert.equal(await page.getByRole('button',{name:'Сохранить черновик',exact:true}).count(),0);
  assert(await page.locator('[data-production-form]').evaluate(el=>{const a=el.getBoundingClientRect(),b=document.querySelector('.main-content').getBoundingClientRect();return Math.abs((a.left-b.left)-(b.right-a.right))<3;}));
  const role=()=>page.locator(`[data-position-plan="${c.role}"]`),person=()=>role().locator('[data-production-person]').first();
  await role().getByRole('button',{name:'Назначить сотрудника'}).click();await person().locator('[name$="-employee"]').selectOption(String(c.employee));
  await saved();assert(await person().locator('[name$="-id"]').inputValue());assert.equal(page.url(),initialUrl);
  assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'1');
  assert(await person().evaluate(el=>el.getBoundingClientRect().height<70));
  // Sticky tabs are checked with a long list, matching the real production screen.
  await page.locator('[data-production-form]').evaluate(el=>{const spacer=document.createElement('div');spacer.dataset.stickyTest='';spacer.style.height='1500px';el.append(spacer);});
  await page.locator('.production-steps').evaluate(el=>scrollTo(0,scrollY+el.getBoundingClientRect().top+180));
  await page.waitForFunction(()=>Math.abs(document.querySelector('.production-steps').getBoundingClientRect().top-(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--navbar-height'))||48))<3);
  await page.locator('[data-sticky-test]').evaluate(el=>el.remove());
  await person().locator('.production-person-details>summary').click();
  // Newer input typed while a save is in flight must survive and be saved next.
  let release,started;const start=new Promise(resolve=>started=resolve),hold=new Promise(resolve=>release=resolve);let intercepted=false;
  await page.route('**/*',async route=>{if(!intercepted&&route.request().method()==='POST'&&(route.request().postData()||'').includes('autosave')){intercepted=true;started();await hold;}await route.continue();});
  await person().locator('[name$="-activity"]').fill('Первый комментарий');
  const flush=page.evaluate(()=>window.productionAutoSave.flush());await start;
  await person().locator('[name$="-activity"]').fill('Последний комментарий');release();assert(await flush);await saved();
  assert.equal(await person().locator('[name$="-activity"]').inputValue(),'Последний комментарий');
  let failNext=true;await page.route('**/*',route=>{if(failNext&&route.request().method()==='POST'&&(route.request().postData()||'').includes('autosave')){failNext=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Временная ошибка сохранения'})});}return route.continue();});
  await person().locator('[name$="-activity"]').fill('Значение после сбоя');assert.equal(await page.evaluate(()=>window.productionAutoSave.flush()),false);
  assert.equal(await person().locator('[name$="-activity"]').inputValue(),'Значение после сбоя');assert((await page.locator('[data-autosave-state]').textContent()).includes('Временная ошибка'));
  await page.locator('[data-autosave-retry]').click();await page.waitForFunction(()=>document.querySelector('[data-autosave-state]').textContent.includes('Все изменения сохранены'));

  await person().locator('.production-person-details>summary').click();
  await role().getByRole('button',{name:'Назначить сотрудника'}).click();const extra=role().locator('[data-production-person]').last();
  assert(await extra.locator(`[name$="-employee"] option[value="${c.employee}"]`).isDisabled());
  await extra.locator('[name$="-employee"]').evaluate((el,id)=>{el.value=String(id);el.dispatchEvent(new Event('change',{bubbles:true}));},c.employee);
  assert.equal(await extra.locator('[name$="-employee"]').inputValue(),'');assert((await extra.locator('[data-error]').textContent()).includes('уже выбран'));
  await extra.locator('[name$="-shift"]').fill('2');await extra.locator('[name$="-shift"]').dispatchEvent('change');
  assert(await extra.locator(`[name$="-employee"] option[value="${c.employee}"]`).isEnabled());
  await extra.locator('[name$="-shift"]').fill('1');await extra.locator('[name$="-shift"]').dispatchEvent('change');
  await extra.locator('[name$="-employee"]').evaluate((el,id)=>el.value=String(id),c.employee);
  const duplicate=await page.evaluate(()=>{try{document.querySelector('[data-production-form]').productionValidatePeople();return '';}catch(error){return error.message;}});assert(duplicate.includes('уже выбран'));assert.equal(page.url(),initialUrl);
  assert.equal(await extra.locator('[name$="-person_editing"]').inputValue(),'0');assert.equal(await person().locator('[name$="-person_editing"]').inputValue(),'1');
  await extra.locator('[name$="-employee"]').selectOption('');await extra.getByRole('button',{name:'Удалить',exact:true}).click();
  await page.locator('[data-step="3"]').click();const card=page.locator('.graph-person');
  assert((await card.locator('[data-graph-person-position]').textContent()).includes(c.role_name));assert((await card.locator('[data-graph-staff-position]').textContent()).includes(c.staff_role));
  await page.locator('[data-step="2"]').click();
  const oldId=await person().locator('[name$="-id"]').inputValue();await person().getByRole('button',{name:'Удалить',exact:true}).click();
  await page.waitForFunction(id=>![...document.querySelectorAll('[data-production-person] input[name$="-id"]')].some(el=>el.value===id),oldId);
  assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'0');
  await role().getByRole('button',{name:'Назначить сотрудника'}).click();await person().locator('[name$="-employee"]').selectOption(String(c.employee));await saved();
  assert.notEqual(await person().locator('[name$="-id"]').inputValue(),oldId);assert.equal(await page.locator('[data-position-total=assigned]').textContent(),'1');
  await person().evaluate(el=>el.scrollIntoView({block:'center'}));await page.screenshot({path:'/tmp/production-autosave-centered.png'});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('PASS: centered panel, sticky tabs, duplicate prevention, automatic save, in-flight input preservation, direct deletion, re-add and mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
