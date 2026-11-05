const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{const c=JSON.parse(process.env.WORK_FILTER_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});try{
 const context=await browser.newContext();await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(c.base+c.path);await page.getByRole('button',{name:'Добавить работу вне СМГ'}).click();const row=page.locator('[data-work-fact-row]').last();
 await row.locator('[name$="-complex_object_filter"]').selectOption(String(c.complex));
 const options=await row.locator('[name$="-project_work"] option').evaluateAll(rows=>rows.map(row=>({value:row.value,text:row.textContent})));
 console.log(JSON.stringify({location:await row.locator('[name$="-complex_object_filter"]').inputValue(),options,errors}));
 assert.deepEqual(options.filter(row=>row.value).map(row=>row.value),[String(c.work)]);assert(options[1].text.includes('старая структура'));
 await row.locator('.production-work-field-project_work .search-select-button').click();const visible=await page.locator('.search-select-popup:not([hidden]) .search-select-option').allTextContents();assert.equal(visible.length,2);assert(visible[1].includes('УЗА 33'));assert.deepEqual(errors,[]);
 console.log('PASS: complex object strictly filters a new searchable work row and legacy work remains selectable.');
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
