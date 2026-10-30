const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{const c=JSON.parse(process.env.ESTIMATE_REVIEW_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const ctx=await browser.newContext({viewport:{width:1440,height:1000}});await ctx.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
 await ctx.route('**/*',r=>{if(r.request().url().startsWith(c.base))return r.continue();if(r.request().url().includes('bootstrap')&&r.request().url().includes('.css'))return r.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return r.abort();});
 const page=await ctx.newPage(),errors=[];let navigations=0;page.on('pageerror',e=>errors.push(e.message));page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++;});
 await page.goto(c.base+c.path);const initialNavigations=navigations;
 assert(await page.locator('.review-header button[value=approve]').isDisabled());assert(await page.locator('#review-bulk').isHidden());
 await page.locator('#review-select-all').check();assert(await page.locator('#review-bulk').isVisible());assert(await page.locator('#review-bulk button[value=bulk_approve]').isEnabled());assert.equal(await page.locator('#review-bulk button[value=composite_setup]').count(),0);
 await page.locator('#review-search').fill('нет такой работы');assert(await page.locator('#review-bulk').isHidden());assert(await page.locator('#review-empty').isVisible());await page.locator('#review-reset').click();assert(await page.locator('.review-work').first().isVisible());
 await page.screenshot({path:'/tmp/estimate-review-wide.png'});await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.getElementById('sidebar').getBoundingClientRect().right<=1);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:'/tmp/estimate-review-mobile.png'});await page.setViewportSize({width:1440,height:1000});
 await page.locator('#review-select-all').check();
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(c.path)),page.locator('#review-bulk button[value=bulk_approve]').click()]);
 await page.waitForFunction(()=>!document.querySelector('.review-header button[value=approve]').disabled);
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(c.path)),page.locator('.review-header button[value=approve]').click()]);
 await page.waitForFunction(()=>document.getElementById('review-final').hidden);assert.equal(navigations,initialNavigations);
 assert.deepStrictEqual(errors,[]);console.log('PASS: inline prices; filters/mobile; asynchronous group approval and completion without navigation');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
