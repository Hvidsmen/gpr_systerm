(() => {
 const tree=document.querySelector('[data-object-plan-tree]');if(!tree)return;
 const workForm=document.getElementById('object-work-editor');
 document.addEventListener('submit',event=>{if(event.target!==workForm&&workForm.dataset.dirty==='true'){event.preventDefault();const header=document.querySelector('[data-plan-action-header]'),status=header.querySelector('[role=status]');if(status){status.textContent='Сначала сохраните введённые объёмы работ.';status.hidden=false;}header.scrollIntoView({block:'nearest'});}});
 tree.querySelectorAll('input[type=number]').forEach(input=>{input.value=input.value.replace(/([.,]\d*?)0+$/,'$1').replace(/[.,]$/,'');});
 document.querySelectorAll('[data-object-plan-expand]').forEach(button=>button.addEventListener('click',()=>tree.querySelectorAll('details').forEach(node=>node.open=button.dataset.objectPlanExpand==='true')));
 document.querySelectorAll('[data-object-plan-open]').forEach(button=>button.addEventListener('click',event=>{if(event.defaultPrevented)return;const target=document.getElementById(button.dataset.objectPlanOpen);if(target)target.open=true;}));
 const search=document.querySelector('[data-object-plan-search]'),count=document.querySelector('[data-object-plan-count]');
 function filter(){const term=search.value.trim().toLocaleLowerCase();let visible=0,total=0;tree.querySelectorAll('[data-object-plan-package]').forEach(node=>{total++;node.hidden=!node.dataset.planWorkName.toLocaleLowerCase().includes(term);if(!node.hidden){visible++;if(term)node.open=true;}});tree.querySelectorAll('[data-object-plan-group],[data-object-plan-section]').forEach(node=>{node.hidden=!node.querySelector('[data-object-plan-package]:not([hidden])');if(term&&!node.hidden)node.open=true;});count.textContent=visible+' / '+total;}
 search.addEventListener('input',filter);filter();
 document.getElementById('object-work-editor').addEventListener('invalid',event=>{for(let node=event.target.parentElement;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;},true);
})();
