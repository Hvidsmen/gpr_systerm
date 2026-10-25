const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const c=JSON.parse(process.env.EMPLOYEE_BROWSER_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await context.route('**/*',route=>route.request().url().startsWith(c.base)?route.continue():route.abort());
  await context.addCookies([{name:'sessionid',value:c.admin_cookie,url:c.base}]);
  const page=await context.newPage();
  await page.goto(c.base+c.listing);
  await page.getByRole('link',{name:'Иванов Иван Иванович',exact:true}).click();
  await page.waitForURL(c.base+c.detail);
  assert(await page.getByRole('heading',{name:'Последнее место работы',exact:true}).isVisible());
  assert(await page.locator('#employment').getByText('Инженер отдела цифровизации',{exact:true}).isVisible());
  assert(await page.locator('#personal').getByText('123456789012',{exact:true}).isVisible());
  assert(await page.locator('#pay').getByText('123 456,78 ₽',{exact:true}).isVisible());
  assert.equal((await page.request.get(c.base+'/static/resources/employee_card.css')).status(),200);
  if(process.env.EMPLOYEE_CARD_SCREENSHOT)await page.screenshot({path:process.env.EMPLOYEE_CARD_SCREENSHOT,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('link',{name:'Личные данные',exact:true}).click();
  assert(await page.locator('#personal').getByText('123456789012',{exact:true}).isVisible());
  assert(await page.locator('.employee-card-grid').evaluate(el=>el.scrollWidth<=el.clientWidth));
  if(process.env.EMPLOYEE_CARD_MOBILE_SCREENSHOT)await page.screenshot({path:process.env.EMPLOYEE_CARD_MOBILE_SCREENSHOT,fullPage:true});
  await context.clearCookies();
  await context.addCookies([{name:'sessionid',value:c.planner_cookie,url:c.base}]);
  await page.goto(c.base+c.detail);
  assert.equal(await page.locator('#personal,#pay').count(),0);
  assert(!(await page.content()).includes('123456789012'));
  assert(await page.locator('#employment').getByText('Инженер отдела цифровизации',{exact:true}).isVisible());
  console.log('PASS: list → employee card; employment and HR fields; mobile layout; planner privacy');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
