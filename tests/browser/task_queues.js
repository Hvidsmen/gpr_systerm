const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.TASK_QUEUE_CONFIG), browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*',route=>{
   const url=route.request().url();
   if(url.startsWith(c.base))return route.continue();
   if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});
   return route.abort();
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.base+c.url);
  const direction=()=>page.getByRole('navigation',{name:'Направление заданий'});
  const stages=()=>page.getByRole('navigation',{name:'Состояние заданий'});
  assert.equal(await direction().locator('[aria-current=page]').textContent(),'Задания мне');
  assert((await page.locator('tbody').textContent()).includes('Входящее поручение'));
  assert(!(await page.locator('tbody').textContent()).includes('Исходящее поручение'));
  await page.getByRole('link',{name:/Входящее поручение/}).click();
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Взять в работу',exact:true}).click()]);
  assert((await page.locator('main').textContent()).includes('Принято в работу'));
  await page.goto(c.base+c.url);
  await stages().getByRole('link',{name:/В работе/}).click();
  assert((await page.locator('tbody').textContent()).includes('Входящее поручение'));
  await direction().getByRole('link',{name:'Задания от меня',exact:true}).click();
  assert.equal(new URL(page.url()).searchParams.get('status'),'in_progress');
  await stages().getByRole('link',{name:/Ещё не взяты/}).click();
  assert((await page.locator('tbody').textContent()).includes('Исходящее поручение'));
  await page.locator('#task-kind').selectOption('MANUAL');
  await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Показать',exact:true}).click()]);
  assert.equal(new URL(page.url()).searchParams.get('scope'),'outgoing');
  await stages().getByRole('link',{name:/История/}).click();
  assert((await page.locator('tbody').textContent()).includes('Прошлое поручение'));
  assert.equal(new URL(page.url()).searchParams.get('kind'),'MANUAL');
  await page.screenshot({path:'/tmp/task-queues-wide.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await stages().getByRole('link',{name:/Ещё не взяты/}).click();
  assert.equal(new URL(page.url()).searchParams.get('scope'),'outgoing');
  assert((await page.locator('tbody').textContent()).includes('Исходящее поручение'));
  await page.screenshot({path:'/tmp/task-queues-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: incoming/outgoing stages, acceptance, preserved filters and mobile layout');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
