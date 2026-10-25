const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const c=JSON.parse(process.env.SHARES_BROWSER_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1500,height:1000}});
  await context.route('**/*',r=>r.request().url().startsWith(c.base)?r.continue():r.abort());
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage();await page.goto(c.base+c.url);
  let form=page.locator('[data-cost-shares]');
  await form.locator('[name=share_'+c.a+']').fill('10');await form.locator('[name=share_'+c.b+']').fill('10');
  assert.equal((await form.locator('[data-cost-share-total]').textContent()).replace(',','.'),'20.00');
  await form.getByRole('button',{name:'Сохранить доли стоимости',exact:true}).click();
  await page.getByText('Сумма долей всех работ комплекса должна быть 100%.',{exact:true}).waitFor();
  form=page.locator('[data-cost-shares]');
  assert.equal(await form.locator('[name=share_'+c.a+']').inputValue(),'10');
  await form.locator('[name=share_'+c.a+']').fill('25');await form.locator('[name=share_'+c.b+']').fill('75');
  await form.getByRole('button',{name:'Сохранить доли стоимости',exact:true}).click();
  await page.getByText('Доли стоимости сохранены. Сумма — 100%.',{exact:true}).waitFor();
  assert.equal(Number((await page.locator('[name=share_'+c.a+']').inputValue()).replace(',','.')),25);
  console.log('PASS: edit cost shares inline; live total; reject incomplete distribution; preserve input; save 100%');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
