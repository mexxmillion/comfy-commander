(() => {
  const form=document.querySelector('#llm-settings-form');
  const original=new Map();
  const selectors=['#ai-model','#qwen-ai-model','#creative-model','.tm-agent-controls select'];
  async function modelLabels() {
    try {
      const response=await fetch('/api/v1/settings/llm',{credentials:'same-origin'});
      if(!response.ok)return;
      const data=await response.json(),config=data.settings;
      window.tensorLLMModel=config.provider==='compatible'?config.model:null;
      for(const selector of selectors) {
        const select=document.querySelector(selector);if(!select)continue;
        if(!original.has(select))original.set(select,select.innerHTML);
        if(config.provider==='compatible') {
          select.replaceChildren(new Option(`${config.model} · configured endpoint`,config.model));
          const label=select.closest('label')?.querySelector('span');if(label)label.textContent='AI endpoint model';
        }else {select.innerHTML=original.get(select);const label=select.closest('label')?.querySelector('span');if(label)label.textContent='OpenRouter model';}
      }
    }catch(_){}
  }
  if(form) {
    const status=document.querySelector('#llm-status');
    function showProvider(){form.querySelectorAll('[data-llm-custom]').forEach(field=>field.hidden=form.elements.provider.value!=='compatible');}
    async function load() {
      try {
        const response=await fetch('/api/v1/settings/llm',{credentials:'same-origin'});
        if(!response.ok)throw new Error('Sign in to configure AI.');
        const data=await response.json();
        for(const [name,value] of Object.entries(data.settings)) {const field=form.elements[name];if(field)field.type==='checkbox'?field.checked=value:field.value=value;}
        document.querySelector('#compatible-llm-key').value='';showProvider();
        status.textContent=data.settings.provider==='openrouter'?'OpenRouter selected. Connect its key using the service card above.':`Compatible endpoint selected. ${data.custom_key_saved?'Key saved for this endpoint.':'No matching saved key; localhost endpoints may be keyless.'} Capabilities must match the actual model.`;
      }catch(error){status.textContent=error.message;}
    }
    async function submit(save) {
      if(!form.reportValidity())return;
      const settings={};for(const field of form.elements){if(!field.name)continue;settings[field.name]=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value;}
      const payload={settings},key=document.querySelector('#compatible-llm-key').value.trim();if(key&&settings.provider==='compatible')payload.api_key=key;
      const buttons=form.querySelectorAll('button');buttons.forEach(button=>button.disabled=true);
      try {
        const response=await fetch('/api/v1/settings/llm'+(save?'':'/validate'),{method:save?'PUT':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const data=await response.json();if(!response.ok)throw new Error(Array.isArray(data.detail)?data.detail.map(item=>item.msg).join('; '):data.detail||'LLM check failed');
        status.textContent=(save?'Saved. ':'Valid; nothing saved. ')+data.message;
        if(save){document.querySelector('#compatible-llm-key').value='';localStorage.setItem('commander-llm-updated',String(Date.now()));await modelLabels();}
      }catch(error){status.textContent=error.message;}
      finally{buttons.forEach(button=>button.disabled=false);}
    }
    form.addEventListener('submit',event=>{event.preventDefault();void submit(true);});
    form.elements.provider.addEventListener('change',showProvider);
    document.querySelector('#validate-llm').addEventListener('click',()=>void submit(false));
    document.querySelector('#remove-compatible-llm-key').addEventListener('click',async()=>{try{const r=await fetch('/api/v1/settings/llm/key',{method:'DELETE',credentials:'same-origin'});if(!r.ok)throw new Error('Sign in before removing the key.');document.querySelector('#compatible-llm-key').value='';status.textContent='Compatible-endpoint key removed. OpenRouter key unchanged.';}catch(error){status.textContent=error.message;}});
    window.addEventListener('commander-local-auth-ready',()=>void load());
    void load();
  }
  window.addEventListener('storage',event=>{if(event.key==='commander-llm-updated')void modelLabels();});
  window.addEventListener('commander-llm-ui-ready',()=>void modelLabels());
  void modelLabels();
})();
