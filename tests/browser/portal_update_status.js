const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const c=JSON.parse(process.env.PORTAL_UPDATE_BROWSER_CONFIG);
const html=fs.readFileSync(c.html,'utf8');
const busy={ready:true,busy:true,state:'updating',version:'a'.repeat(40),stage:'Скачивание новой версии',stage_number:2,stage_total:10,progress:10,message:'Обновление выполняется в фоне.'};
const done={...busy,busy:false,state:'done',version:'b'.repeat(40),stage:'',stage_number:0,progress:100,message:'ГОТОВО. Обновление завершено, портал доступен.'};
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  for(const failure of ['401','redirect','403','offline']){
   const context=await browser.newContext();
   const page=await context.newPage();
   let mode='busy',posts=0;
   await context.route('**/*',async route=>{
    const request=route.request();
    if(request.method()==='POST')posts++;
    const url=new URL(request.url());
    if(url.pathname===c.page)return route.fulfill({contentType:'text/html',body:html});
    if(url.pathname===c.status){
     if(mode==='offline')return route.abort();
     if(mode==='redirect')return route.fulfill({status:302,headers:{Location:'/accounts/login/'}});
     if(['401','403'].includes(mode))return route.fulfill({status:Number(mode),body:'Access denied'});
     return route.fulfill({contentType:'application/json',body:JSON.stringify(mode==='done'?done:busy)});
    }
    if(url.pathname==='/accounts/login/')return route.fulfill({contentType:'text/html',body:'Login'});
    return route.fulfill({contentType:request.resourceType()==='stylesheet'?'text/css':'text/javascript',body:''});
   });
   await page.clock.install();
   await page.goto('http://portal.invalid'+c.page);
   await page.addScriptTag({path:c.source});
   await page.waitForFunction(()=>document.querySelector('#portal-update [data-update-stage]').textContent==='Скачивание новой версии');
   mode=failure;
   await page.clock.fastForward(3001);
   await page.waitForFunction(()=>document.querySelector('#portal-update [data-update-progress]').hidden);
   assert.equal(await page.locator('#portal-update [data-update-stage]').textContent(),'');
   assert(await page.locator('#portal-update [data-update-submit]').isDisabled());
   assert.equal(await page.locator('#portal-update [data-update-fill]').evaluate(el=>el.classList.contains('progress-bar-animated')),false);
   const loginRequired=['401','redirect'].includes(failure);
   assert.equal(await page.locator('#portal-update [data-update-login]').isVisible(),loginRequired);
   const loginURL=new URL(await page.locator('#portal-update [data-update-login]').getAttribute('href'),'http://portal.invalid');
   assert.equal(loginURL.searchParams.get('next'),c.page);
   if(failure==='403')assert((await page.locator('#portal-update [data-update-message]').textContent()).includes('Нет доступа'));
   mode='done';
   await page.clock.fastForward(failure==='offline'?3001:15001);
   await page.waitForFunction(()=>document.querySelector('#portal-update [data-update-message]').textContent.startsWith('ГОТОВО'));
   assert(await page.locator('#portal-update [data-update-progress]').isVisible());
   assert.equal(await page.locator('#portal-update [data-update-progress]').getAttribute('aria-valuenow'),'100');
   assert(await page.locator('#portal-update [data-update-login]').isHidden());
   assert(await page.locator('#portal-update [data-update-submit]').isEnabled());
   assert.equal(await page.locator('#portal-update [data-update-version]').textContent(),'b'.repeat(40));
   assert.equal(posts,0,'Status recovery must never enqueue another update');
   await context.close();
   console.log('Update status recovered after '+failure);
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
