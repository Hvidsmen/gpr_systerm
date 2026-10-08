// npm install --no-save playwright && npx playwright install chromium
// node tests/forms.browser.test.cjs (CHROMIUM_PATH may select a system browser)
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}),
    args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent(`<main class="main-content"><form id="form">
      <label for="resource">Бригада</label><div id="original-parent">
      <select name="resource" id="resource" required><option value="">---------</option>
      <option value="1">Сварочно-монтажная бригада</option><option value="2">Буровая бригада</option>
      <option value="3" disabled>Недоступная бригада</option></select></div>
      <label for="objects">Объекты</label><select name="objects" id="objects" multiple>
      <option value="a">Объект Ёлка</option><option value="b">Строительный объект</option></select>
      <fieldset id="locked" disabled><select id="disabled"><option>Закрытый</option></select></fieldset>
      <div id="rows"></div><button type="submit">Сохранить</button><button type="reset">Сбросить</button>
      </form></main>`);
    await page.addStyleTag({path:path.resolve('static/core/forms.css')});
    await page.addScriptTag({path:path.resolve('static/core/forms.js')});
    const popup = page.locator('.search-select-popup:not([hidden])');
    const single = page.locator('#original-parent .search-select-button');
    assert.equal(await page.locator('#resource').evaluate(s => s.parentElement.id), 'original-parent');
    await page.evaluate(() => {window.changed=0;document.querySelector('#resource').addEventListener('change',()=>window.changed++);});
    await page.locator('button[type=submit]').click();
    await page.locator('.search-select-validation:not([hidden])').waitFor();
    await single.click();
    await popup.locator('input').fill('свар монтаж');
    assert.equal(await popup.locator('[role=option]').count(), 1);
    await popup.locator('input').press('Enter');
    assert.equal(await page.locator('#resource').inputValue(), '1');
    assert.equal(await page.evaluate(() => window.changed), 1);
    assert.equal(await page.evaluate(() => new FormData(document.querySelector('#form')).get('resource')), '1');
    await single.press('ArrowDown');
    await popup.locator('input').fill('несуществующий');
    assert.equal(await popup.locator('[role=status]').textContent(), 'Ничего не найдено');
    await popup.locator('input').press('Escape');
    assert.equal(await single.getAttribute('aria-expanded'), 'false');
    const multi = page.locator('#objects + .search-select-shell .search-select-button');
    await multi.click();
    await popup.locator('input').fill('елка');
    await popup.locator('[role=option]').click();
    await popup.locator('input').fill('строит');
    await popup.locator('[role=option]').click();
    assert.deepEqual(await page.evaluate(() => new FormData(document.querySelector('#form')).getAll('objects')), ['a','b']);
    await popup.locator('.search-select-clear').click();
    assert.deepEqual(await page.evaluate(() => new FormData(document.querySelector('#form')).getAll('objects')), []);
    await popup.locator('input').press('Escape');
    assert.equal(await page.locator('#locked .search-select-button').isDisabled(), true);
    await page.locator('#locked').evaluate(f=>f.disabled=false);
    await page.waitForFunction(()=>!document.querySelector('#locked .search-select-button').disabled);
    await page.evaluate(() => {
      const s=document.querySelector('#resource');s.innerHTML='<option value="9">Новая бригада</option>';
      document.querySelector('#rows').innerHTML='<select name="dynamic"><option value="d">Добавленная строка</option></select>';
    });
    await page.waitForFunction(()=>document.querySelector('#original-parent .search-select-label').textContent==='Новая бригада');
    await page.locator('#rows .search-select-button').waitFor();
    await single.click();
    assert.equal(await popup.locator('[role=option]').count(),1);
    const box=await popup.boundingBox();
    assert.ok(box.x>=0 && box.x+box.width<=391, 'Popup fits mobile viewport');
    await popup.locator('input').press('Escape');
    await page.locator('button[type=reset]').click();
    assert.equal(await page.locator('#resource').inputValue(),'9');
    await page.locator('#rows select').evaluate(s=>s.remove());
    await page.waitForFunction(()=>!document.querySelector('#rows .search-select-shell'));
    assert.deepEqual(errors, []);
    console.log('Searchable forms: passed validation, search, keyboard, multiple values, dependent options, dynamic rows, disabled fields and mobile layout.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
