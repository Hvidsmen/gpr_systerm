const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.CATALOGUE_REVIEW_CONFIG);
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const errors=[];
  async function actor(name){
   const context=await browser.newContext({viewport:{width:1500,height:1000},locale:'ru-RU'});
   await context.addCookies([{name:'sessionid',value:c.cookies[name],url:c.base}]);
   await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();const css=process.env.CATALOGUE_BOOTSTRAP_CSS||'/tmp/catalogue-bootstrap.css';if(url.includes('bootstrap.min.css')&&fs.existsSync(css))return route.fulfill({contentType:'text/css',body:fs.readFileSync(css)});return route.abort();});
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));return page;
  }
  const planner=await actor('planner'),manager=await actor('manager'),hr=await actor('hr'),tech=await actor('tech');
  async function submit(page,button){const [response]=await Promise.all([page.waitForNavigation({waitUntil:'load'}),button.click()]);assert(response.status()<400,'HTTP '+response.status()+': '+(await page.locator('.errorlist,.app-form-errors,.alert-danger').allTextContents()).join(' '));const errors=(await page.locator('.errorlist,.app-form-errors').allTextContents()).join(' ').trim();assert.equal(errors,'','Validation: '+errors+' '+(await page.locator('.alert-danger').allTextContents()).join(' '));}
  async function propose(page,plan,kind,name){
   await page.goto(c.base+plan);
   await page.getByRole('link',{name:kind==='labor'?'Создать должность':'Создать технику для планирования',exact:true}).click();await page.waitForLoadState('load');
   await page.locator('[name=name]').fill(name);
   await submit(page,page.getByRole('button',{name:'Создать и добавить в план'}));
   assert((await page.locator('[data-catalogue-plan-review]').innerText()).includes(name));
   assert((await page.locator('[data-catalogue-plan-review]').innerText()).includes('На проверке'));
  }
  async function setCount(page,label,value){
   const target=page.locator(`[data-plan-month-input][aria-label^="${label} ·"]`).first();
   await target.fill(value);
   await submit(page,page.getByRole('button',{name:'Сохранить',exact:true}).first());
   assert.equal(await page.locator(`[data-plan-month-input][aria-label^="${label} ·"]`).first().inputValue(),value,'Count was not retained: '+(await page.locator('.errorlist,.app-form-errors,.alert-danger').allTextContents()).join(' '));
  }
  await propose(planner,c.gpr,'labor','Новая должность браузера');
  await setCount(planner,'Новая должность браузера','3');
  await propose(manager,c.gpr,'equipment','Новая техника браузера');
  await setCount(manager,'Новая техника браузера','2');
  await submit(manager,manager.getByRole('button',{name:'Ресурсы определены',exact:true}));
  assert(await manager.getByRole('button',{name:'Отправить на согласование',exact:true}).isDisabled());
  await hr.goto(c.base+c.queue);await hr.getByRole('link',{name:'Новая должность браузера',exact:true}).click();await hr.waitForLoadState('load');
  await hr.locator('[name=action]').selectOption('replace');await hr.locator('[name=target]').selectOption(String(c.target));
  await submit(hr,hr.getByRole('button',{name:'Продолжить',exact:true}));
  assert(await hr.locator('[data-catalogue-preview]').isVisible());
  assert.match(await hr.locator('[data-catalogue-preview] tbody').innerText(),/3\s+2\s+5/);
  await hr.screenshot({path:'/tmp/catalogue-review-replacement.png',fullPage:true});
  await submit(hr,hr.getByRole('button',{name:'Подтвердить замену в планах'}));
  assert((await hr.locator('body').innerText()).includes('Заменено существующей'));
  await tech.goto(c.base+c.queue);assert.equal(await tech.getByRole('link',{name:'Новая должность браузера',exact:true}).count(),0);
  await tech.getByRole('link',{name:'Новая техника браузера',exact:true}).click();await tech.waitForLoadState('load');
  await submit(tech,tech.getByRole('button',{name:'Продолжить',exact:true}));
  assert((await tech.locator('body').innerText()).includes('Подтверждено'));
  await manager.goto(c.base+c.gpr);assert(!(await manager.locator('[data-catalogue-plan-review]').innerText()).includes('заблокирована'));
  await submit(manager,manager.getByRole('button',{name:'Ресурсы определены',exact:true}));
  assert(await manager.getByRole('button',{name:'Отправить на согласование',exact:true}).isEnabled());
  await submit(manager,manager.getByRole('button',{name:'Отправить на согласование',exact:true}));
  await propose(planner,c.smg,'labor','Отклоняемая должность браузера');
  await hr.goto(c.base+c.queue);await hr.getByRole('link',{name:'Отклоняемая должность браузера',exact:true}).click();await hr.waitForLoadState('load');
  await hr.locator('[name=action]').selectOption('reject');await hr.locator('[name=comment]').fill('Выберите действующую должность');
  await submit(hr,hr.getByRole('button',{name:'Продолжить',exact:true}));
  await planner.goto(c.base+c.smg);assert((await planner.locator('[data-catalogue-plan-review]').innerText()).includes('Выберите действующую должность'));
  await planner.getByRole('link',{name:'Заменить в этом плане',exact:true}).click();await planner.waitForLoadState('load');
  await planner.locator('[name=target]').selectOption(String(c.target));await submit(planner,planner.getByRole('button',{name:'Предпросмотр замены',exact:true}));
  await submit(planner,planner.getByRole('button',{name:'Подтвердить замену в планах'}));
  assert.equal(await planner.locator('[data-catalogue-plan-review]').count(),1); // creation controls stay available
  assert(!(await planner.locator('[data-catalogue-plan-review]').innerText()).includes('заблокирована'));
  await planner.setViewportSize({width:390,height:844});assert(await planner.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await planner.screenshot({path:'/tmp/catalogue-review-smg-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: planner/manager proposals, quantities, separate reviewer queues, replacement preview, confirmation, submission gate, rejection and correction in SMG, mobile');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
