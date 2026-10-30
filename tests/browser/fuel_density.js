const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const c=JSON.parse(process.env.FUEL_DENSITY_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
const context=await browser.newContext({viewport:{width:1440,height:1080},locale:'ru-RU'});await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap.min.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));const url=c.base+c.url+'?camp='+c.camp+'&date='+c.day;
await page.goto(url);const form=page.locator('[data-camp-fuel-form]');assert(await form.isVisible());
const start=page.locator('[data-fuel-opening-mass]'),end=page.locator('[data-fuel-closing-mass]');assert.equal(await start.innerText(),'—');assert.equal(await end.innerText(),'—');
async function fill(name,value){await form.locator('[name='+name+']').fill(value);}
await fill('opening','10');await fill('received','5');await fill('consumed','3');await fill('opening_density','0.840');await fill('closing_density','0.830');
assert.equal(await start.innerText(),'8,400 т');assert.equal(await end.innerText(),'9,960 т');assert.equal(await page.locator('[data-fuel-preview]').innerText(),'12,000 м³');
assert.equal(await start.evaluate(el=>el.tagName),'OUTPUT');assert.equal(await end.evaluate(el=>el.tagName),'OUTPUT');
await fill('opening','0.001');await fill('received','0');await fill('consumed','0');await fill('opening_density','0.500000');assert.equal(await start.innerText(),'0,001 т');
await fill('opening_density','');assert.equal(await start.innerText(),'—');
await fill('opening','10');await fill('received','5');await fill('consumed','3');await fill('opening_density','0.840');
await page.screenshot({path:'/tmp/fuel-density-desktop.png',fullPage:true,animations:'disabled'});
await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'Сохранить баланс',exact:true}).click()]);
assert.equal((await page.locator('[data-fuel-saved-opening-mass]').innerText()).replace(/\s+/g,' '),'8,400 т');assert.equal((await page.locator('[data-fuel-saved-closing-mass]').innerText()).replace(/\s+/g,' '),'9,960 т');
await page.setViewportSize({width:390,height:844});await page.waitForTimeout(400);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Density page overflows mobile');await page.screenshot({path:'/tmp/fuel-density-mobile.png',fullPage:true,animations:'disabled'});
const next=new Date(c.day+'T12:00:00Z');next.setUTCDate(next.getUTCDate()+1);await page.goto(c.base+c.url+'?camp='+c.camp+'&date='+next.toISOString().slice(0,10));
assert(await form.locator('[name=opening]').isDisabled());assert.equal(Number(await form.locator('[name=opening_density]').inputValue()),0.83);assert(!(await form.locator('[name=opening_density]').isDisabled()));assert.equal(await end.innerText(),'—');
assert.equal(await start.innerText(),'9,960 т');assert.deepEqual(errors,[]);console.log('Fuel density: exact tonnes, rounding, unknown density, save/reload, carried opening density and mobile passed.');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
