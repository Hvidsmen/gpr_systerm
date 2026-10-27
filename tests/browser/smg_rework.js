const {chromium} = require('playwright'), fs = require('fs'), assert = require('assert');
(async () => {
  const c = JSON.parse(process.env.SMG_REWORK_CONFIG);
  const browser = await chromium.launch({executablePath:c.chromium, headless:true, args:['--no-sandbox']});
  const errors = [];
  async function pageFor(cookie) {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addCookies([{name:'sessionid', value:cookie, url:c.base}]);
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(c.base)) return route.continue();
      if (url.includes('bootstrap.min.css')) return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});
      return route.abort();
    });
    const page = await context.newPage();page.on('pageerror', e => errors.push(e.message));
    return page;
  }
  const planner = await pageFor(c.planner_cookie), admin = await pageFor(c.cookie);
  await planner.goto(c.base+c.path);
  assert.strictEqual(await planner.locator('[data-smg-open-rework]').count(),0);
  await admin.goto(c.base+c.path);
  const open = admin.locator('[data-smg-open-rework]'), dialog = admin.locator('[data-smg-rework]');
  await open.click();assert(await dialog.isVisible());
  assert(!(await dialog.locator('form').evaluate(form => form.checkValidity())),'Reason is mandatory');
  await dialog.locator('[data-smg-close-rework]').click();assert(!(await dialog.isVisible()));
  await open.click();await admin.keyboard.press('Escape');assert(!(await dialog.isVisible()));
  await admin.setViewportSize({width:390,height:844});await open.click();
  await dialog.locator('[name=comment]').fill('Уточнить дневные объёмы после проверки');
  await admin.screenshot({path:'/tmp/smg-rework-mobile.png',animations:'disabled'});
  const box = await dialog.boundingBox();assert(box.x>=0&&box.x+box.width<=390);
  await admin.setViewportSize({width:1440,height:1000});
  await admin.screenshot({path:'/tmp/smg-rework-dialog.png',animations:'disabled'});
  await Promise.all([admin.waitForNavigation(),dialog.getByRole('button',{name:'Вернуть в доработку',exact:true}).click()]);
  assert.strictEqual(new URL(admin.url()).pathname,c.path);
  assert((await admin.locator('.plan-action-title .badge').innerText()).includes('На доработке'));
  assert((await admin.locator('[data-smg-action-header]').innerText()).includes('Уточнить дневные объёмы после проверки'));
  assert.strictEqual(await admin.locator('[data-smg-open-rework]').count(),0);
  const closed = admin.locator(`[data-entry-id="${c.entry}"] [data-smg-day-cell][data-date="2026-02-01"]`);
  assert.strictEqual(await closed.locator('input').count(),0,'Closed day stays read-only');
  const editableDay = admin.locator(`[data-entry-id="${c.entry}"] .smg-day-input[data-field=quantity][data-date="2026-02-02"]`);
  assert(!(await editableDay.isDisabled()));await editableDay.fill('130');
  const response = admin.waitForResponse(r => r.url()===c.base+c.path&&r.request().method()==='POST');
  await admin.locator('#smg-save-table').click();assert((await response).ok());
  await admin.waitForFunction(()=>document.getElementById('smg-save-status').dataset.state==='saved');
  await admin.screenshot({path:'/tmp/smg-rework-editable.png',animations:'disabled'});
  assert.deepStrictEqual(errors,[]);await browser.close();
  console.log('PASS: admin-only return, mandatory reason, cancel/Escape, mobile dialog, same editable revision, closed-day protection and saved corrections');
})().catch(error=>{console.error(error);process.exit(1);});
