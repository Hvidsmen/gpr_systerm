const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const c=JSON.parse(process.env.WORK_OPENING_CONFIG);
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const errors=[];
  async function pageFor(cookie){
   const ctx=await browser.newContext({viewport:{width:1440,height:1000},locale:'ru-RU'});
   await ctx.addCookies([{name:'sessionid',value:cookie,url:c.base}]);
   await ctx.route('**/*',r=>{if(r.request().url().startsWith(c.base))return r.continue();if(r.request().url().includes('bootstrap.min.css')&&fs.existsSync('/tmp/catalogue-bootstrap.css'))return r.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});return r.abort();});
   const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));return page;
  }
  const admin=await pageFor(c.admin);
  await admin.goto(c.base+c.fact);
  await admin.getByRole('link',{name:'Входящие остатки работ',exact:true}).click();await admin.waitForLoadState('load');
  await admin.locator(`a[href="${c.edit}"]`).click();await admin.waitForLoadState('load');
  await admin.locator('[name=date_work]').fill(c.day);
  await admin.locator('[name=quantity_work]').fill('125');
  await admin.locator('[name=reason]').fill('Начальный объём по акту');
  const [saved]=await Promise.all([admin.waitForNavigation(),admin.getByRole('button',{name:'Сохранить входящие остатки',exact:true}).click()]);
  assert.equal(saved.status(),200);
  assert.match(await admin.locator('body').innerText(),/Входящие остатки сохранены/);
  assert.match(await admin.locator('body').innerText(),/125/);
  await admin.getByText('История изменений',{exact:true}).click();
  assert.match(await admin.locator('body').innerText(),/Начальный объём по акту/);
  await admin.screenshot({path:'/tmp/work-opening-admin.png',fullPage:true});
  await admin.goto(c.base+c.list+'?construction_object='+c.object);
  assert.match(await admin.locator('tbody').innerText(),/125/);
  const dispatcher=await pageFor(c.dispatcher);
  await dispatcher.goto(c.base+c.fact);
  assert.equal(await dispatcher.getByRole('link',{name:'Входящие остатки работ',exact:true}).count(),0);
  assert.match(await dispatcher.locator('body').innerText(),/125/);
  const denied=await dispatcher.goto(c.base+c.edit);assert.equal(denied.status(),403);
  assert.deepEqual(errors,[]);
  console.log('PASS: administrator enters opening stock with history; daily card sees previous stock; dispatcher has no edit access');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
