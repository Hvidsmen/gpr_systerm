const {chromium}=require('playwright'),assert=require('assert'),path=require('path');
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});try{const page=await browser.newPage({viewport:{width:1200,height:900}});await page.setContent(`
<form data-production-form>
 <section data-work-fact-row><input name="works-0-actual_quantity" type="number" value="2"><input name="works-0-comment"><textarea name="works-0-note"></textarea><button type="button" data-action>Кнопка</button></section>
 <section data-work-fact-row><input name="works-1-actual_quantity" type="number" value="4"><input name="works-1-comment"><textarea name="works-1-note"></textarea></section>
 <section data-production-person><select name="labor-0-employee"><option>Первый</option></select><select name="labor-0-shift"><option>День</option></select></section>
 <section data-production-person><select name="labor-1-employee"><option>Второй</option></select><select name="labor-1-shift"><option>Ночь</option></select></section>
 <div class="production-crew-person"><input type="checkbox"><span>А</span><select><option>1</option></select></div><div class="production-crew-person"><input type="checkbox"><span>Б</span><select><option>2</option></select></div>
 <div data-employee-graph><div class="graph-work employee-graph-card">Работа</div><details class="employee-graph-crew"><summary><div class="graph-person graph-crew employee-graph-card">Бригада</div></summary><div><div class="graph-person employee-graph-card">Сотрудник</div></div></details><svg><path class="graph-edge-hit" aria-label="Связь"></path></svg></div>
</form>`);await page.addScriptTag({path:path.resolve(__dirname,'../../static/production/keyboard_navigation.js')});
 const active=()=>page.evaluate(()=>document.activeElement.getAttribute('name')||document.activeElement.textContent.trim());
 await page.locator('[name="works-0-actual_quantity"]').focus();await page.keyboard.press('ArrowDown');assert.equal(await active(),'works-1-actual_quantity');
 await page.locator('[name="works-0-actual_quantity"]').focus();await page.keyboard.press('ArrowRight');assert.equal(await active(),'works-0-comment');
 await page.locator('[name="works-0-actual_quantity"]').focus();await page.keyboard.press('Control+ArrowUp');assert.equal(await page.locator('[name="works-0-actual_quantity"]').inputValue(),'3');
 await page.locator('[name="works-0-note"]').fill('текст');await page.locator('[name="works-0-note"]').evaluate(el=>el.setSelectionRange(2,2));await page.keyboard.press('ArrowLeft');assert.equal(await active(),'works-0-note');
 await page.locator('[name="works-0-note"]').fill('');await page.keyboard.press('ArrowLeft');assert.equal(await active(),'works-0-actual_quantity');
 await page.locator('[name="works-0-note"]').focus();await page.keyboard.press('ArrowRight');assert.equal(await active(),'works-1-actual_quantity');
 await page.locator('[name="labor-0-shift"]').focus();await page.keyboard.press('ArrowDown');assert.equal(await active(),'labor-1-shift');
 await page.locator('.production-crew-person').first().locator('input').focus();await page.keyboard.press('ArrowDown');assert(await page.locator('.production-crew-person').nth(1).locator('input').evaluate(el=>el===document.activeElement));
 const work=page.locator('.graph-work');await work.focus();await page.keyboard.press('ArrowRight');assert(await page.locator('.graph-crew').evaluate(el=>el===document.activeElement));await page.keyboard.press('ArrowRight');assert(await page.locator('.employee-graph-crew').evaluate(el=>el.open));
 await page.evaluate(()=>{window.edgeClicks=0;window.edgeMenus=0;const edge=document.querySelector('.graph-edge-hit');edge.addEventListener('click',()=>edgeClicks++);edge.addEventListener('contextmenu',()=>edgeMenus++);});const edge=page.locator('.graph-edge-hit');await edge.focus();await page.keyboard.press('Enter');await page.keyboard.press('Delete');assert.deepEqual(await page.evaluate(()=>[edgeClicks,edgeMenus]),[1,1]);
 console.log('PASS: keyboard navigation across works, people, crews and links');}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
