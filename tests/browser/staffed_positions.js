const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs');
const c=JSON.parse(process.env.STAFFED_POSITIONS_CONFIG);
(async()=>{
 const browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000}});
  await context.route('**/*',route=>{
   const url=route.request().url();if(url.startsWith(c.base))return route.continue();
   if(url.endsWith('/bootstrap.min.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('/tmp/catalogue-bootstrap.css')});
   return route.abort();
  });
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  for(const plan of c.plans){
   await page.goto(c.base+plan.url,{waitUntil:'networkidle'});
   await page.waitForFunction(()=>Boolean(window.gprResourceEditor)).catch(async error=>{
    console.error('Page:',page.url(),'Errors:',errors,'Scripts:',await page.locator('script[src]').evaluateAll(nodes=>nodes.map(node=>node.src)));
    throw error;
   });
   await page.evaluate(()=>window.gprResourceEditor.open('labor'));
   const editor=page.locator('[data-inline-resource-editor]'),position=editor.locator('[name=position]');
   let ids=await position.locator('option').evaluateAll(options=>options.map(option=>option.value));
   assert(ids.includes(String(c.staffed)));assert(!ids.includes(String(c.saved)));assert(!ids.includes(String(c.empty)));
   await editor.locator('[data-resource-field=labor] .search-select-button').click();
   const popup=page.locator('.search-select-popup:visible');await popup.getByRole('searchbox').fill(c.staffed_name);
   await popup.getByRole('option',{name:c.staffed_name,exact:true}).click();
   assert.equal(await position.inputValue(),String(c.staffed));
   await editor.locator('[data-close-resource]').click();
   await page.evaluate(id=>window.gprResourceEditor.open('labor','','',window.gprResourceEditor.data.assignments.find(row=>row.id===id)),plan.assignment);
   assert.equal(await position.inputValue(),String(c.saved));
   await page.waitForFunction(name=>document.querySelector('[data-inline-resource-editor] [data-resource-field=labor] .search-select-button')?.textContent.includes(name),c.saved_name);
   await editor.locator('[name=count]').fill('3');
   const pending=page.waitForResponse(response=>response.request().method()==='POST'&&response.headers()['content-type']?.includes('application/json'));
   await editor.getByRole('button',{name:'Сохранить ресурс',exact:true}).click();
   const response=await pending;assert(response.ok(),await response.text());
   assert.equal((await response.json()).assignment.position,c.saved);
   await editor.waitFor({state:'detached'});
   await page.evaluate(()=>window.gprResourceEditor.open('labor'));
   ids=await position.locator('option').evaluateAll(options=>options.map(option=>option.value));
   assert(!ids.includes(String(c.saved)),'Editing the old assignment must not add it to new choices');
   await editor.locator('[data-close-resource]').click();
  }
  assert.deepEqual(errors,[]);console.log('PASS: GPR and SMG staffed search, archived assignment edit, save, and isolated new choices');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
