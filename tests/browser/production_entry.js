const {chromium}=require('playwright');const fs=require('fs');const assert=require('assert');
(async()=>{
 const c=JSON.parse(process.env.PRODUCTION_ENTRY_CONFIG),browser=await chromium.launch({executablePath:c.chromium,headless:true,args:['--no-sandbox']}),errors=[];
 async function open(cookie){const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});await context.addCookies([{name:'sessionid',value:cookie,url:c.base}]);await context.route('**/*',route=>{const url=route.request().url();if(url.startsWith(c.base))return route.continue();if(url.includes('bootstrap')&&url.includes('.css'))return route.fulfill({body:fs.readFileSync('/tmp/catalogue-bootstrap.css'),contentType:'text/css'});return route.abort();});const page=await context.newPage();page.on('response',async response=>{if(response.status()>=500){try{const body=await response.text();fs.writeFileSync('/tmp/production-actuation-browser-error.html',body);console.error('HTTP',response.status(),response.url(),body.match(/<pre class="exception_value">([\s\S]*?)<\/pre>/)?.[1]||body.slice(0,200));}catch{}}});page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR:',e.message);});page.on('dialog',dialog=>dialog.dismiss());await page.goto(c.base+c.path);return page;}
 async function save(page){assert(await page.evaluate(()=>window.productionAutoSave.flush()),await page.locator('[data-autosave-state]').textContent());}
 const a=await open(c.cookie),b=await open(c.second_cookie),work=(page,id)=>page.locator('[data-work-fact-row]').filter({has:page.locator(`[name$="-project_work"][value="${id}"]`)}),person=page=>page.locator(`[data-position-plan="${c.planned_position}"] [data-production-person]`).first();
 assert.equal(await a.locator('[name$="-deviation_reason"]').count(),0);assert.equal(await a.locator('[data-deviation-dialog]').count(),0);
 await a.waitForFunction(()=>document.querySelectorAll('.production-presence-user').length===2);
 assert.equal(await a.locator('[data-take]:visible').count(),0);assert.equal(await a.locator('[data-confirm-production]').count(),0);
 await work(a,c.work).locator('[name$="-actual_quantity"]').fill('5');await save(a);
 await b.waitForFunction(id=>[...document.querySelectorAll('[data-work-fact-row]')].some(r=>r.querySelector('[name$="-project_work"]')?.value===String(id)&&r.querySelector('[name$="-actual_quantity"]').value==='5'),c.work);
 assert(await work(b,c.work).locator('[name$="-actual_quantity"]').isDisabled());
 await work(b,c.second_work).locator('[name$="-actual_quantity"]').fill('3');await save(b);
 await a.locator('[data-step="2"]').click();await a.locator(`[data-add-for-position="${c.planned_position}"]`).click();await person(a).locator('[name$="-employee"]').selectOption(String(c.employee));await save(a);
 await b.locator('[data-step="2"]').click();await b.waitForFunction(id=>[...document.querySelectorAll('[data-production-person]')].some(r=>r.querySelector('[name$="-employee"]')?.value===String(id)&&r.querySelector('[name$="-id"]').value),c.employee);
 assert(await person(b).locator('[name$="-activity"]').isDisabled());
 await b.locator('[data-step="3"]').click();await b.locator(`.graph-work[data-token="work:${c.work}"]`).click();await b.locator('.graph-person .graph-port').click();await b.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===1);
 await b.locator(`.graph-work[data-token="work:${c.second_work}"]`).click();await b.locator('.graph-person .graph-port').click();await b.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===2);
 await a.locator('[data-step="3"]').click();await a.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===2);
 assert((await a.locator('.graph-person').innerText()).includes('Должность из СМГ'));assert((await a.locator('.graph-work strong').first().innerText()).length>0);
 // Filters permit selecting multiple values without changing form facts.
 const filter=a.locator('[data-production-filters="graph"] .rg-multi').filter({has:a.locator('summary',{hasText:/^Работы ·/})});await filter.locator('summary').click();const checkboxes=filter.locator('input[type=checkbox]');await checkboxes.nth(0).check();await checkboxes.nth(1).check();assert.equal(await filter.locator('input[type=checkbox]:checked').count(),2);await filter.getByRole('button',{name:'Сбросить'}).click();await filter.locator('summary').click();
 await a.locator('[data-graph-work-search]').fill('несуществующая');assert.equal(await a.locator('.graph-work:visible').count(),0);await a.locator('[data-graph-work-search]').fill('');
 await a.locator('[data-employee-graph]').evaluate(el=>el.scrollIntoView({block:'start'}));await a.screenshot({path:'/tmp/production-collaboration-graph.png',animations:'disabled'});
 await a.setViewportSize({width:390,height:844});assert(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await a.screenshot({path:'/tmp/production-collaboration-mobile.png',animations:'disabled'});await a.setViewportSize({width:1440,height:1000});
 await a.locator('[data-step="4"]').click();assert((await a.locator('[data-report-people]').innerText()).includes('Иванов'));assert(await a.locator('.production-report-group-role').count());
 const download=await Promise.all([a.waitForEvent('download'),a.locator('[data-report-download]').click()]);assert(download[0].suggestedFilename().endsWith('.xlsx'));await download[0].saveAs('/tmp/production-browser-report.xlsx');assert.equal(fs.readFileSync('/tmp/production-browser-report.xlsx').subarray(0,2).toString(),'PK');
 // Zero removes only this work link; other work links remain.
 await a.locator('[data-step="1"]').click();await work(a,c.work).locator('[name$="-actual_quantity"]').fill('0');await save(a);await a.waitForFunction(id=>![...document.querySelectorAll('[name$="-work_targets"]')].some(i=>i.value==='work:'+id),c.work);
 await b.waitForFunction(()=>[...document.querySelectorAll('.graph-work')].some(el=>el.textContent.includes('Объём работы — 0')));assert.equal(await b.locator('[data-graph-lines] path').count(),1);
 // Return to 5 so a colleague edit later leaves a meaningful 5 -> 4 audit.
 await work(a,c.work).locator('[name$="-actual_quantity"]').fill('5');await save(a);
 await a.locator('[data-step="3"]').click();await a.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===1);await a.locator(`.graph-work[data-token="work:${c.work}"]`).click();await a.locator('.graph-person .graph-port').click();await a.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===2);
 // The explicit clear button also saves zero and removes this work's links.
 await a.locator('[data-step="1"]').click();await work(a,c.work).getByRole('button',{name:'Очистить работу',exact:true}).click();await save(a);assert.equal(await work(a,c.work).locator('[name$="-actual_quantity"]').inputValue(),'0');await a.waitForFunction(id=>![...document.querySelectorAll('[name$="-work_targets"]')].some(i=>i.value==='work:'+id),c.work);await b.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===1);
 await work(a,c.work).locator('[name$="-actual_quantity"]').fill('5');await save(a);await a.locator('[data-step="3"]').click();await a.locator(`.graph-work[data-token="work:${c.work}"]`).click();await a.locator('.graph-person .graph-port').click();await a.waitForFunction(()=>document.querySelectorAll('[data-graph-lines] path').length===2);
 // Release the saved data leases before switching to the reviewer workflow.
 await a.evaluate(async()=>{const form=document.querySelector('[data-production-form]');for(const row of form.querySelectorAll('[data-work-fact-row],[data-production-person]')){if(row.querySelector('[name$="-work_editing"],[name$="-person_editing"]')?.value==='1')await form.productionReleaseRow(row);}});
 await a.close();await b.locator('[data-step="1"]').click();await b.waitForFunction(id=>[...document.querySelectorAll('[data-work-fact-row]')].find(r=>r.querySelector('[name$="-project_work"]')?.value===String(id))?.querySelector('[name$="-actual_quantity"]')?.disabled===false,c.work,{timeout:15000});
 await work(b,c.work).locator('[name$="-actual_quantity"]').fill('4');await save(b);
 assert.equal(await work(b,c.work).locator('[data-work-remove]').isHidden(),true);
 // Per-work actuation: two leaves selected together, a third work stays untouched.
 await b.locator('[data-work-actuation-open]').click();const act=b.locator('[data-work-actuation-dialog]');
 await act.locator('[data-actuation-search]').fill('кабеля');await act.locator('[data-actuation-select]').click();
 assert.equal(await act.locator('input[type=checkbox]:checked').count(),1);
 await act.locator('[data-actuation-search]').fill('');
 await act.locator(`[data-work-token="item:${c.package}:${c.camera}"] input`).check();
 assert((await act.locator('[data-actuation-count]').textContent()).includes('2'));
 await act.locator('[data-actuation-apply]').click();assert((await act.locator('[data-actuation-error]').textContent()).includes('причину'));
 await act.locator('[data-bulk-actuation-reasons] input').nth(0).check();await act.locator('[data-bulk-actuation-reasons] input').nth(1).check();
 await act.locator('[data-actuation-reason-new]').click();const proposal=b.locator('[data-actuation-reason-dialog]');
 await proposal.locator('[data-new-reason-name]').fill('Сильный ветер');await proposal.locator('[data-new-reason-save]').click();await proposal.waitFor({state:'hidden'});assert(await proposal.locator('[data-new-reason-save]').isEnabled());
 assert.equal(await act.locator('[data-bulk-actuation-reasons] input:checked').count(),3);
 await act.locator('[data-bulk-actuation-files]').setInputFiles([{name:'Акт.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-browser-act')},{name:'Фото.png',mimeType:'image/png',buffer:Buffer.from('PNG-browser-photo')}]);
 await b.setViewportSize({width:390,height:844});await b.waitForTimeout(400);
 assert(await b.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await b.screenshot({path:'/tmp/work-actuation-mobile.png',animations:'disabled'});
 await b.setViewportSize({width:1440,height:1000});await b.screenshot({path:'/tmp/work-actuation-desktop.png',animations:'disabled'});
 await act.locator('[data-actuation-apply]').click();await act.waitFor({state:'hidden'});
 assert.equal(await work(b,c.work).locator('[name$="-actual_quantity"]').inputValue(),'4');
 for(const item of [c.camera,c.cable]){const leaf=b.locator('[data-work-fact-row]').filter({has:b.locator(`[name$="-work_item"][value="${item}"]`)});assert.equal(await leaf.locator('[name$="-actual_quantity"]').inputValue(),'0');assert.equal(await leaf.locator('[name$="-actuation_type"]').inputValue(),'FULL_DAY');}
 await b.locator('[data-step="4"]').click();assert((await b.locator('[data-report-works]').textContent()).includes('Сильный ветер'));
 const actExport=await Promise.all([b.waitForEvent('download'),b.locator('[data-report-download]').click()]);await actExport[0].saveAs('/tmp/production-actuation-report.xlsx');
 // Clear only one leaf from the same picker, then restore its quantity individually.
 await b.locator('[data-step="1"]').click();await b.locator('[data-work-actuation-open]').click();
 await act.locator(`[data-work-token="item:${c.package}:${c.camera}"] input`).check();await act.locator('[data-actuation-type]').selectOption('');
 await act.locator('[data-actuation-apply]').click();await act.waitFor({state:'hidden'});
 const camera=b.locator('[data-work-fact-row]').filter({has:b.locator(`[name$="-work_item"][value="${c.camera}"]`)});
 assert.equal(await camera.locator('[name$="-actuation_type"]').inputValue(),'');assert.equal(await camera.locator('[name$="-actual_quantity"]').inputValue(),'0');
 await camera.locator('[name$="-actual_quantity"]').fill('2');await save(b);
 const cable=b.locator('[data-work-fact-row]').filter({has:b.locator(`[name$="-work_item"][value="${c.cable}"]`)});
 assert.equal(await cable.locator('[name$="-actuation_type"]').inputValue(),'FULL_DAY');
 // Individual actuation also clears only this row.
 await camera.locator('[name$="-actuation_type"]').selectOption('DAY');await camera.locator('input[name$="-actuation_reasons"]').first().check();await save(b);
 // Files survive removing actuation and can be downloaded by name.
 assert.equal(await camera.locator('[data-actuation-documents] a').count(),2);
 const file=await Promise.all([b.waitForEvent('download'),camera.locator('[data-actuation-documents] a').first().click()]);assert(file[0].suggestedFilename().endsWith('.pdf'));
 // A newly proposed row reason is reviewed by the production head through their tasks.
 await camera.locator('[data-actuation-reason-new]').click();await proposal.locator('[data-new-reason-name]').fill('Проблема с автобусом');await proposal.locator('[data-new-reason-save]').click();await proposal.waitFor({state:'hidden'});await save(b);
 const head=await open(c.head_cookie);await head.goto(c.base+c.reason_list);
 await head.locator('tr').filter({hasText:'Проблема с автобусом'}).getByRole('link',{name:'Открыть'}).click();
 await head.locator('[name="action"]').selectOption('replace');const target=await head.locator('[name="target"] option').filter({hasText:'погодным'}).getAttribute('value');await head.locator('[name="target"]').selectOption(target);await head.getByRole('button',{name:'Подтвердить решение'}).click();assert((await head.locator('body').textContent()).includes('Заменена существующей'));
 await head.goto(c.base+c.reason_list);await head.locator('tr').filter({hasText:'Сильный ветер'}).getByRole('link',{name:'Открыть'}).click();await head.locator('[name="action"]').selectOption('approve');await head.getByRole('button',{name:'Подтвердить решение'}).click();assert((await head.locator('body').textContent()).includes('Принята'));
 await b.reload();await b.locator('[data-step="1"]').click();
 await camera.locator('[data-actuation-reason-new]').click();await proposal.locator('[data-new-reason-name]').fill('Ошибка выбора причины');await proposal.locator('[data-new-reason-save]').click();await proposal.waitFor({state:'hidden'});await save(b);
 await head.goto(c.base+c.reason_list);await head.locator('tr').filter({hasText:'Ошибка выбора причины'}).getByRole('link',{name:'Открыть'}).click();await head.locator('[name="action"]').selectOption('reject');await head.getByRole('button',{name:'Подтвердить решение'}).click();assert((await head.locator('body').textContent()).includes('Удалена из списка'));
 await head.screenshot({path:'/tmp/actuation-reason-review.png',animations:'disabled'});
 await b.reload();await b.locator('[data-step="1"]').click();assert((await camera.locator('[data-actuation-reasons]').textContent()).includes('удалена руководителем'));
 assert.equal(await camera.locator('[data-actuation-documents] a').count(),2);
 await camera.locator('[data-actuation-documents] button').first().click();await camera.locator('[data-actuation-documents] a').first().waitFor();await b.waitForFunction(id=>[...document.querySelectorAll('[data-work-fact-row]')].find(r=>r.querySelector('[name$="-work_item"]')?.value===String(id))?.querySelectorAll('[data-actuation-documents] a').length===1,c.camera);
 await head.close();
 assert.equal(await camera.locator('[name$="-actual_quantity"]').inputValue(),'0');
 assert.equal(await work(b,c.work).locator('[name$="-actual_quantity"]').inputValue(),'4');
 assert.deepStrictEqual(errors,[]);await browser.close();console.log('PASS: reason checklist, proposal, role task review approve/replace/reject, multi-file bulk upload, private download, soft removal, automatic leases, live presence/data, independent live links, grouping, multi-select filters, compact graph, zero and clear button remove only matching work edges, XLSX download, lease release, colleague edit, mobile');
})().catch(e=>{console.error(e);process.exit(1)});
