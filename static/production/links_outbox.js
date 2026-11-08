/* Durable link intents, isolated by account/company/object/day/reset generation. */
(()=>{'use strict';
 window.ProductionLinksOutbox=class{
  constructor(form,callbacks){
   this.form=form;this.callbacks=callbacks;this.running=false;this.attempt=0;this.timer=null;this.blocked=false;this.blockedReason='';this.storageTimer=null;
   this.prefix=['yds-links-v1',form.dataset.companyId,form.dataset.userId,form.elements.construction_object.value,form.elements.date.value,form.dataset.resetEpoch||'0'].join(':')+':';
   this.host=form.querySelector('[data-links-sync]');this.records=[];this.load();
   this.host?.querySelector('[data-links-retry]')?.addEventListener('click',()=>{this.blocked=false;this.attempt=0;this.flush();});
   this.host?.addEventListener('click',event=>{const remove=event.target.closest('[data-links-dismiss]');if(remove){this.remove(remove.dataset.linksDismiss);this.changed();}});
   addEventListener('online',()=>{this.blocked=false;this.attempt=0;this.render();});
   addEventListener('storage',event=>{if(event.key?.startsWith(this.prefix)){clearTimeout(this.storageTimer);this.storageTimer=setTimeout(()=>{this.load();this.changed();},80);}});
   addEventListener('beforeunload',event=>{if(this.pending){event.preventDefault();event.returnValue='';}});
   setTimeout(()=>this.changed(),0);
  }
  get pending(){return this.records.filter(r=>!r.rejected).length;}
  get busy(){return this.running||this.pending>0;}
  uuid(){const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;return [...b].map((v,i)=>([4,6,8,10].includes(i)?'-':'')+v.toString(16).padStart(2,'0')).join('');}
  load(){
   const records=[];try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith(this.prefix))continue;try{const record=JSON.parse(localStorage.getItem(key));if(record&&record.id&&(Number.isInteger(record.activity)||Number.isInteger(record.crew)))records.push(record);}catch{}}}catch{}
   this.records=records.sort((a,b)=>a.order-b.order||a.id.localeCompare(b.id));
  }
  put(record){localStorage.setItem(this.prefix+record.id,JSON.stringify(record));}
  remove(id){try{localStorage.removeItem(this.prefix+id);}catch{}this.records=this.records.filter(r=>r.id!==id);}
  enqueue(row,operation){
   if(this.form.dataset.authChanged==='1')throw new Error('Учётная запись изменилась. Откройте форму заново.');
   if(this.form.dataset.resetRequired==='1')throw new Error('День сброшен. Обновите форму перед новым вводом.');
   const id=this.uuid(),activity=Number(row.querySelector('[name$="-id"]').value);
   if(!activity)throw new Error('Дождитесь сохранения сотрудника перед назначением работ.');
   const record={id,activity,...operation,order:Math.max(Date.now(),...this.records.map(r=>r.order+1)),label:row.querySelector('[name$="-employee"]').selectedOptions[0]?.textContent?.split(' · ')[0]||'Сотрудник'};
   // The revision shown in the row includes this tab's pending commands.
   if(record.action==='mode')record.revision=Number(row.dataset.linksRevision||0);
   try{this.put(record);}catch{throw new Error('Браузер не смог сохранить изменение. Освободите место или повторите действие; связь пока не изменена.');}
   this.records.push(record);this.records.sort((a,b)=>a.order-b.order||a.id.localeCompare(b.id));this.changed();
   return {queued:true};
  }
  enqueueCrew(crew,activities,operation){
   if(this.form.dataset.authChanged==='1')throw new Error('Учётная запись изменилась. Откройте форму заново.');
   if(this.form.dataset.resetRequired==='1')throw new Error('День сброшен. Обновите форму перед новым вводом.');
   const id=this.uuid(),record={id,crew:Number(crew),activities:activities.map(Number),...operation,order:Math.max(Date.now(),...this.records.map(r=>r.order+1)),label:'Бригада'};
   try{this.put(record);}catch{throw new Error('Браузер не смог сохранить изменение бригады. Освободите место и повторите действие.');}
   this.records.push(record);this.records.sort((a,b)=>a.order-b.order||a.id.localeCompare(b.id));this.changed();return {queued:true};
  }
  overlay(report){
   const people=report.people.map(p=>({...p,targets:[...p.targets]})),byId=new Map(people.map(p=>[Number(p.id),p]));
   const facts=new Map(report.works.map(w=>[Number(w.id),w]));
   for(const command of this.records){if(command.rejected)continue;const commandPeople=command.action?.startsWith('crew_')?(command.activities||[]).map(id=>byId.get(id)).filter(Boolean):[byId.get(command.activity)].filter(Boolean);for(const person of commandPeople){
    if(command.action==='mode'){person.targets=[];person.general=command.mode==='general';person.reviewed=person.general;}
    else{
     const edges=command.action==='delta'?command.edges:[{action:command.action==='crew_add'?'add':command.action==='crew_remove'?'remove':command.action,fact:command.fact}];const targets=new Set(person.targets);
     for(const edge of edges||[]){const fact=facts.get(Number(edge.fact));if(!fact)continue;const token=fact.operational_name?`operational:${fact.id}`:fact.work_item_id?`item:${fact.project_work_id}:${fact.work_item_id}`:`work:${fact.project_work_id}`;if(edge.action==='remove')targets.delete(token);else if(Number(fact.actual_quantity)>0)targets.add(token);}
     person.targets=[...targets];person.general=false;person.reviewed=person.targets.length>0;
    }
    person.links_revision=Number(person.links_revision||0)+1;person.pending_links=(person.pending_links||0)+1;
   }
   }
   return {...report,people};
  }
  cancelPerson(id){for(const record of [...this.records])if(record.activity===Number(id))this.remove(record.id);this.changed();}
  cancelWork(id){
   for(const record of [...this.records]){
    if(record.action==='mode')continue;
    if(record.action==='delta'){
     const edges=record.edges.filter(edge=>Number(edge.fact)!==Number(id));if(edges.length===record.edges.length)continue;
     this.remove(record.id);if(edges.length){const replacement={...record,id:this.uuid(),edges};delete replacement.rejected;this.put(replacement);this.records.push(replacement);}
    }else if(Number(record.fact)===Number(id))this.remove(record.id);
   }
   this.records.sort((a,b)=>a.order-b.order||a.id.localeCompare(b.id));this.changed();
  }
  changed(){
   this.form.dataset.linksPending=String(this.pending);this.form.dataset.linksRejected=String(this.records.filter(r=>r.rejected).length);this.callbacks.changed();this.render();
   this.form.dispatchEvent(new CustomEvent('production-links-state',{detail:{pending:this.pending,rejected:this.records.filter(r=>r.rejected).length}}));
  }
  render(message){
   if(!this.host)return;const status=this.host.querySelector('[data-links-status]'),pending=this.pending;
   status.textContent=message||(pending?(navigator.onLine===false?'Нет соединения. ':this.running?'Отправляем изменения. ':'')+`Связей в очереди: ${pending}. Черновик сохранён в этом браузере.`:this.records.some(r=>r.rejected)?'Некоторые связи не сохранены — проверьте сообщения ниже.':'Все изменения связей сохранены');
   status.classList.toggle('text-success',!pending&&!this.records.some(r=>r.rejected));
   const button=this.host.querySelector('[data-links-retry]');button.hidden=!pending;button.disabled=this.running;
   const errors=this.host.querySelector('[data-links-errors]');errors.replaceChildren();
   for(const record of this.records.filter(r=>r.rejected)){const item=document.createElement('div');item.className='small text-danger d-flex gap-2 align-items-start';const text=document.createElement('span');text.textContent=record.label+': '+record.rejected+' Проверьте данные и повторите нужное действие.';const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='btn btn-sm btn-outline-secondary';dismiss.dataset.linksDismiss=record.id;dismiss.textContent='Понятно';item.append(text,dismiss);errors.append(item);}
  }
  async send(explicit=false){
   if(this.running||this.blocked||!this.pending)return;
   if(navigator.onLine===false){this.render();return;}
   if((!explicit&&window.productionAutoSave?.running)||this.form.dataset.peopleBulkBusy==='1'||this.form.dataset.personShiftBusy==='1')return;
   this.running=true;this.render();const batch=this.records.filter(r=>!r.rejected).slice(0,20);
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
   try{
    const {scope,csrf}=this.form.productionSession,dataDigest=this.callbacks.digest();
    const commands=batch.map(({label,order,rejected,activities,...command})=>command);
    const response=await fetch(this.form.dataset.linksUrl,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({...scope,protocol:'queued-v1',data_digest:dataDigest,commands})});
    if(response.redirected||response.status===401||response.status===403){
     const details=response.headers.get('content-type')?.includes('application/json')?await response.json():{};
     this.blocked=true;this.blockedReason=details.error||'Проверьте вход и права в портале. Изменения сохранены в этом браузере; откройте форму заново под своей учётной записью.';if(details.auth_changed||response.redirected)this.form.dataset.authChanged='1';this.render(this.blockedReason);return;
    }
    if(![200,400,409].includes(response.status)||!response.headers.get('content-type')?.includes('application/json'))throw new Error('Не удалось связаться с порталом. Изменения остаются в очереди.');
    const result=await response.json();
    if(!response.ok){
     for(const record of batch){record.rejected=result.error||'Пакет не принят сервером.';this.put(record);}if(result.reset_required){this.blocked=true;this.blockedReason=result.error;this.form.dataset.resetRequired='1';}
    }else{
     const results=new Map(result.results.map(r=>[r.id,r]));
     for(const record of batch){if(!localStorage.getItem(this.prefix+record.id))continue;const receipt=results.get(record.id);if(!receipt)throw new Error('Ответ сервера неполный. Повторим отправку безопасно.');if(receipt.ok)this.remove(record.id);else{record.rejected=receipt.error;this.put(record);}}
     this.callbacks.people(result.people||[],result.removed||[]);if(result.digest&&dataDigest===this.callbacks.digest())this.callbacks.ackDigest(result.digest);this.attempt=0;
    }
    this.load();this.changed();
   }catch(error){this.attempt++;this.render('Связь нестабильна. Изменения сохранены в этом браузере; отправим при восстановлении соединения.');}
   finally{clearTimeout(timeout);this.running=false;this.form.dispatchEvent(new CustomEvent('production-network-time',{detail:{slow:this.attempt>0}}));this.render(this.blocked?this.blockedReason:undefined);}
  }
  async flush(){
   const deadline=Date.now()+30000,total=this.pending;
   while(this.pending||this.running){
    if(this.blocked||navigator.onLine===false||this.attempt>0||Date.now()>deadline)return false;
    if(!this.running){await this.send(true);const done=total-this.pending;this.form.productionStatus?.update(`Сохранено связей: ${done} из ${total}`,done,total);}
    if(this.pending||this.running)await new Promise(resolve=>setTimeout(resolve,50));
   }
   return !this.records.some(r=>r.rejected);
  }

 };
})();
