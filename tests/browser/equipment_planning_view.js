const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
 const c = JSON.parse(process.env.EQUIPMENT_PLANNING_VIEW_CONFIG);
 const browser = await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']});
 try {
  const context = await browser.newContext({viewport:{width:1500,height:1000}});
  await context.addCookies([{name:'sessionid',value:c.cookie,url:c.base}]);
  await context.route('**/*', route => {
   const url = route.request().url();
   if (url.startsWith(c.base)) return route.continue();
   if (url.includes('bootstrap') && url.includes('.css')) {
    const css = process.env.CATALOGUE_BOOTSTRAP_CSS || '/tmp/catalogue-bootstrap.css';
    return fs.existsSync(css) ? route.fulfill({contentType:'text/css',body:fs.readFileSync(css)}) : route.continue();
   }
   return route.abort();
  });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const types = page.locator('[data-equipment-type]');
  const root = page.locator(`[data-equipment-type="${c.root}"]`);
  await page.goto(c.base+c.catalogue);
  assert.equal(await page.locator('[data-equipment-category]').count(),3);
  await page.getByRole('button',{name:'Свернуть все',exact:true}).click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-equipment-type]')].every(n => !n.open));
  await page.getByRole('link',{name:'По технике для планирования',exact:true}).click();
  await page.waitForLoadState('load');
  assert.equal(await page.locator('[data-equipment-category]').count(),0);
  assert.equal(await types.count(),2);
  assert.equal(await root.count(),1);
  assert.equal(await page.locator('.equipment-catalogue-viewbar [aria-current="page"]').textContent(),'По технике для планирования');
  await page.getByRole('button',{name:'Развернуть все',exact:true}).click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-equipment-type]')].every(n => n.open));
  assert.equal(await root.locator('tbody tr').count(),1);
  assert.match(await root.locator('tbody tr').innerText(),/АТЗ \/ Камаз/);
  await page.locator('[data-equipment-select-page]').click();
  assert.equal(await page.locator('[data-bulk-row]:checked').count(),3);
  await page.locator('[data-bulk-clear]').click();
  assert.equal(await page.locator('[data-bulk-row]:checked').count(),0);
  await page.reload();
  assert.equal(await types.evaluateAll(nodes => nodes.every(n => n.open)),true,'planning folds after reload: '+await page.evaluate(() => JSON.stringify(localStorage)));
  await page.screenshot({path:'/tmp/equipment-planning-view-wide.png'});
  await page.getByRole('link',{name:'По группам',exact:true}).click();
  await page.waitForLoadState('load');
  assert.equal(await page.locator('[data-equipment-category]').evaluateAll(nodes => nodes.every(n => !n.open)),true,'category folds after switching: '+await page.evaluate(() => JSON.stringify(localStorage)));
  await page.getByRole('link',{name:'Таблица',exact:true}).click();
  await page.waitForLoadState('load');
  assert.equal(await types.count(),0);
  assert.equal(await page.locator('[data-bulk-row]').count(),3);
  await page.getByRole('link',{name:'По технике для планирования',exact:true}).click();
  await page.waitForLoadState('load');
  await page.locator('#id_name').fill('Камаз');
  await page.getByRole('button',{name:'Применить',exact:true}).click();
  await page.waitForLoadState('load');
  assert.equal(new URL(page.url()).searchParams.get('view'),'planning');
  assert.equal(await types.count(),1);
  assert.equal(await root.evaluate(n => n.open),true);
  assert.equal(await root.locator('[data-bulk-row]').count(),1);
  const form = page.getByRole('form',{name:'Поиск и фильтры техники'});
  assert.equal(await form.locator('[name=view]').inputValue(),'planning');
  await page.getByRole('link',{name:'Сбросить',exact:true}).click();
  await page.waitForLoadState('load');
  assert.equal(new URL(page.url()).searchParams.get('view'),'planning');
  assert.equal(await types.count(),2);
  await page.setViewportSize({width:390,height:844});
  const switcher = page.getByRole('link',{name:'По технике для планирования',exact:true});
  const bounds = await switcher.boundingBox();
  assert(bounds.x >= 0 && bounds.x+bounds.width <= 391);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
  await page.screenshot({path:'/tmp/equipment-planning-view-mobile.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS: three views, unique planning type across categories, bulk selection, independent folds, filters, mobile, no JS errors');
 } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
