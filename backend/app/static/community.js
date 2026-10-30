const jsonRequest = async (url, options = {}) => {
  const response = await fetch(url, {credentials: 'same-origin', headers: {'Content-Type': 'application/json', ...(options.headers || {})}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(item=>`${item.loc?.slice(1).join('.') || 'Value'}: ${item.msg}`).join('; ') : data.detail || 'Request failed');
  return data;
};

const formatBytes = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
const escapeHtml = value => String(value).replace(/[&<>'"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[character]));
const setFormStatus = (form, message, error = false) => {
  const status = form.querySelector('small[role="status"]');
  status.textContent = message;
  status.classList.toggle('error', error);
};
const selectedProfiles = name => [...document.querySelectorAll(`input[name="${name}-profiles"]:checked`)].map(input => input.value);

const wizardPanels = [...document.querySelectorAll('[data-wizard-step]')];
const railButtons = [...document.querySelectorAll('.wizard-rail [data-step-target]')];
const settingsMode = document.body.dataset.settingsMode === 'true';
let currentStep = 0;
let computeMode = localStorage.getItem('commander-compute-mode') || '';
let diagnostics = {};
const renderReadiness = () => {
  const target = document.querySelector('#readiness');
  if (!target) return;
  const connected = [...document.querySelectorAll('.service-card .status.connected')].map(item => item.closest('.service-card').querySelector('h3').textContent);
  const gpu = diagnostics.gpus?.length ? `${diagnostics.gpus.length} local GPU${diagnostics.gpus.length > 1 ? 's' : ''}` : 'No local GPU detected';
  target.innerHTML = `<article><span>COMPUTE</span><strong>${escapeHtml(computeMode || 'Choose later')}</strong><small>${escapeHtml(gpu)}</small></article><article><span>SERVICES</span><strong>${connected.length ? connected.length + ' connected' : 'None required yet'}</strong><small>${escapeHtml(connected.join(' · ') || 'Bring your own keys when ready')}</small></article><article><span>STARTER PROFILES</span><strong>Qwen + MiniMax H3</strong><small>16 GB usable floor; scale heavy jobs up</small></article><article><span>REMOTE ACCESS</span><strong>${diagnostics.tailscale_available ? 'Tailscale detected' : 'Optional'}</strong><small>Private HTTPS from phone or tablet</small></article>`;
};
const showStep = index => {
  currentStep = Math.max(0, Math.min(index, wizardPanels.length - 1));
  wizardPanels.forEach((panel, i) => panel.classList.toggle('active', i === currentStep));
  railButtons.forEach((button, i) => { button.classList.toggle('active', Number(button.dataset.stepTarget) === currentStep); button.classList.toggle('complete', !settingsMode && i < currentStep); });
  if (currentStep === 6) renderReadiness();
  if (currentStep === 5) void loadImportedProfiles();
  if (currentStep === 4) void loadPhoneAccess();
  window.scrollTo({top: 0, behavior: 'smooth'});
};
document.querySelectorAll('[data-next]').forEach(button => button.addEventListener('click', () => showStep(currentStep + 1)));
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => showStep(currentStep - 1)));
document.querySelectorAll('[data-step-target]').forEach(button => button.addEventListener('click', () => showStep(Number(button.dataset.stepTarget))));
document.querySelectorAll('[data-compute]').forEach(card => {
  card.classList.toggle('selected', card.dataset.compute === computeMode);
  card.addEventListener('click', () => {
    computeMode = card.dataset.compute;
    localStorage.setItem('commander-compute-mode', computeMode);
    document.querySelectorAll('[data-compute]').forEach(item => item.classList.toggle('selected', item === card));
    document.querySelector('#compute-next').disabled = false;
  });
});
if (computeMode) document.querySelector('#compute-next').disabled = false;
fetch('/api/v1/setup/diagnostics').then(response => response.json()).then(data => {
  diagnostics = data;
  void loadPhoneAccess();
  const gpuText = data.gpus.length ? data.gpus.map(gpu => `${gpu.name} · ${gpu.vram_gb} GB`).join(' · ') : 'No NVIDIA GPU detected';
  document.querySelector('#diagnostics').innerHTML = `<span class="${data.gpus.length ? 'good' : ''}">${escapeHtml(gpuText)}</span><span class="${data.comfyui_reachable ? 'good' : ''}">${data.comfyui_reachable ? 'ComfyUI is responding' : 'No local ComfyUI running'}</span><span class="${data.tailscale_available ? 'good' : ''}">${data.tailscale_available ? 'Tailscale available' : 'Tailscale optional'}</span>`;
}).catch(() => { document.querySelector('#diagnostics').textContent = 'Local checks unavailable; cloud-only setup still works.'; });

document.querySelector('#password-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const password = document.querySelector('#password').value;
  const confirmPassword = document.querySelector('#password-confirm')?.value;
  if (confirmPassword !== undefined && password !== confirmPassword) return alert('Passwords do not match.');
  const button = event.submitter;
  button.disabled = true;
  try {
    const endpoint = form.dataset.mode === 'login' ? 'login' : 'password-setup';
    await jsonRequest(`/api/v1/cloud/${endpoint}`, {method: 'POST', body: JSON.stringify({password, remember: true})});
    button.textContent = 'Settings unlocked ✓';
    document.querySelector('#protect-next').disabled = false;
    await loadLoras();
    await loadPhoneAccess();
    await loadCloudSsh();
    if(settingsMode) await loadPreferences();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
});

if (document.body.dataset.firstRun === 'false') document.querySelector('#protect-next').disabled = false;
document.querySelector('#manage-models')?.addEventListener('click', () => {
  if(settingsMode) showStep(8);
  document.querySelector('#lora-management').hidden = false;
  document.querySelector('#lora-management').scrollIntoView({behavior: 'smooth'});
});
showStep(settingsMode && location.hash !== '#access' ? 7 : 0);

document.querySelectorAll('.service-card').forEach(card => {
  card.querySelector('form').addEventListener('submit', async event => {
    event.preventDefault();
    const input = card.querySelector('input');
    const button = event.submitter;
    const status = card.querySelector('small[role="status"]');
    button.disabled = true;
    status.classList.remove('error');
    status.textContent = 'Testing without starting a rental…';
    try {
      const data = await jsonRequest('/api/v1/setup/secret', {method: 'POST', body: JSON.stringify({service: card.dataset.service, key: input.value})});
      input.value = '';
      status.textContent = `${data.message} ✓`;
      card.querySelector('.status').textContent = 'Connected';
      card.querySelector('.status').classList.add('connected');
      card.querySelector('.remove-secret').hidden = false;
      if (card.dataset.service === 'huggingface') await loadLoras();
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('error');
    } finally {
      button.disabled = false;
    }
  });
  card.querySelector('.remove-secret').addEventListener('click', async event => {
    if (!confirm(`Remove the locally stored ${card.dataset.service} credential? This does not revoke it at the provider.`)) return;
    event.currentTarget.disabled = true;
    try {
      const data = await jsonRequest(`/api/v1/setup/secret/${card.dataset.service}`, {method: 'DELETE'});
      card.querySelector('.status').textContent = data.connected ? 'Connected by environment' : 'Not connected';
      card.querySelector('.status').classList.toggle('connected', data.connected);
      card.querySelector('small[role="status"]').textContent = data.message;
      event.currentTarget.hidden = !data.connected;
      event.currentTarget.disabled = false;
    } catch (error) {
      card.querySelector('small[role="status"]').textContent = error.message;
      event.currentTarget.disabled = false;
    }
  });
});

const renderProfiles = profiles => {
  document.querySelectorAll('[data-profile-picker]').forEach(fieldset => {
    const name = fieldset.dataset.profilePicker;
    fieldset.innerHTML = `<legend>Use with profiles</legend>${profiles.map(profile => `<label class="check"><input type="checkbox" name="${name}-profiles" value="${profile}">${profile}</label>`).join('')}`;
  });
};

const renderRegistered = entries => {
  const target = document.querySelector('#registered-loras');
  if (!entries.length) {
    target.innerHTML = '<p>No personal LoRAs registered yet.</p>';
    return;
  }
  target.innerHTML = entries.map(item => `<article><div><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.repo)} · ${escapeHtml(item.revision.slice(0, 9))}… · ${formatBytes(item.size)}</span><small>${item.profiles.map(escapeHtml).join(' · ')}</small></div><button class="quiet unregister-lora" data-id="${escapeHtml(item.id)}" type="button">Unregister</button></article>`).join('');
  target.querySelectorAll('.unregister-lora').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Remove this LoRA from future worker bundles? No model file will be deleted.')) return;
    button.disabled = true;
    try {
      await jsonRequest(`/api/v1/setup/loras/${button.dataset.id}`, {method: 'DELETE'});
      await loadLoras();
    } catch (error) {
      alert(error.message);
      button.disabled = false;
    }
  }));
};

async function loadLoras() {
  try {
    const data = await jsonRequest('/api/v1/setup/loras');
    renderProfiles(data.profiles);
    const select = document.querySelector('#local-lora');
    select.innerHTML = '<option value="">Choose a local LoRA</option>' + data.local.map(item => `<option value="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${formatBytes(item.size)}</option>`).join('');
    renderRegistered(data.registered);
  } catch (error) {
    document.querySelector('#registered-loras').innerHTML = `<p>${escapeHtml(error.message)}. Sign in above to manage models.</p>`;
  }
}

document.querySelector('#local-lora')?.addEventListener('change', event => {
  const name = event.target.selectedOptions[0]?.dataset.name;
  if (name) document.querySelector('#upload-path').value = name;
});

document.querySelector('#lora-upload-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const profiles = selectedProfiles('upload');
  if (!profiles.length) return setFormStatus(form, 'Choose at least one profile.', true);
  if (!confirm('Upload this local file to the selected Hugging Face repository and add it to future worker bundles?')) return;
  const button = event.submitter;
  button.disabled = true;
  setFormStatus(form, 'Uploading and verifying…');
  try {
    await jsonRequest('/api/v1/setup/loras/upload', {method: 'POST', body: JSON.stringify({local_id: document.querySelector('#local-lora').value, repo_id: document.querySelector('#upload-repo').value, repo_path: document.querySelector('#upload-path').value, private: document.querySelector('#upload-private').checked, profiles})});
    setFormStatus(form, 'Uploaded, verified, and pinned ✓');
    await loadLoras();
  } catch (error) {
    setFormStatus(form, error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#lora-register-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const profiles = selectedProfiles('register');
  if (!profiles.length) return setFormStatus(form, 'Choose at least one profile.', true);
  const button = event.submitter;
  button.disabled = true;
  setFormStatus(form, 'Verifying immutable file metadata…');
  try {
    await jsonRequest('/api/v1/setup/loras/register', {method: 'POST', body: JSON.stringify({repo_id: document.querySelector('#register-repo').value, revision: document.querySelector('#register-revision').value, file: document.querySelector('#register-file').value, profiles})});
    setFormStatus(form, 'Verified and registered ✓');
    await loadLoras();
  } catch (error) {
    setFormStatus(form, error.message, true);
  } finally {
    button.disabled = false;
  }
});

loadLoras();

async function loadPhoneAccess() {
  const section = document.querySelector('#phone-access');
  const status = document.querySelector('#phone-access-status');
  const input = document.querySelector('#phone-access-url');
  const copy = document.querySelector('#copy-phone-url');
  try {
    const data = await jsonRequest('/api/v1/setup/phone-access');
    section.hidden = false;
    input.value = data.url || '';
    document.querySelector('#enable-phone-access').hidden = !data.can_enable || !!data.url;
    copy.disabled = !data.url;
    document.querySelector('#phone-access-setup').hidden = data.state !== 'not_shared';
    status.textContent = data.url ? `Ready for phone · private HTTPS port ${data.https_port}` :
      !data.available ? 'Install Tailscale using the download button, sign in on both devices, then refresh.' :
      data.state === 'not_shared' ? 'Tailscale is connected. Enable private sharing to get your phone URL.' :
      data.state === 'disconnected' ? 'Open Tailscale on this computer and sign in, then refresh.' :
      'Could not read Tailscale sharing status. Check Tailscale on this computer, then refresh.';
  } catch {
    section.hidden = false;
    input.value = '';copy.disabled = true;
    document.querySelector('#enable-phone-access').hidden = true;
    status.textContent = 'Sign in to Commander settings to view the private phone URL.';
  }
}
document.querySelector('#refresh-phone-access').addEventListener('click', loadPhoneAccess);
document.querySelector('#copy-phone-url').addEventListener('click', async () => {
  const input = document.querySelector('#phone-access-url');
  try {
    await navigator.clipboard.writeText(input.value);
    document.querySelector('#phone-access-status').textContent = 'URL copied. Open it on your phone with Tailscale connected.';
  } catch {
    input.focus();input.select();
    document.querySelector('#phone-access-status').textContent = 'Select and copy the URL above.';
  }
});
loadPhoneAccess();

document.querySelector('#enable-phone-access').addEventListener('click', async event => {
  if (!confirm('Enable private HTTPS access to this Commander through your Tailscale network? Devices allowed by your tailnet can reach the sign-in page.')) return;
  const button = event.currentTarget;button.disabled = true;
  try {
    await jsonRequest('/api/v1/setup/phone-access', {method: 'POST'});
    await loadPhoneAccess();
  } catch (error) {
    document.querySelector('#phone-access-status').textContent = error.message;
  } finally { button.disabled = false; }
});

let importedWorkflow = null;
const importPayload = () => ({name: document.querySelector('#import-profile-name').value.trim() || 'Imported H3 profile', base_profile: document.querySelector('#import-profile-base').value, workflow: importedWorkflow});
const importReport = text => { document.querySelector('#import-profile-report').textContent = text; };
for (const selector of ['#import-profile-name','#import-profile-base']) document.querySelector(selector).addEventListener('input', () => {document.querySelector('#save-import-profile').disabled=true;});
document.querySelector('#import-profile-file').addEventListener('change', async event => {
  importedWorkflow = null;window.dispatchEvent(new CustomEvent('commander:profile-workflow',{detail:null}));document.querySelector('#save-import-profile').disabled = true;
  const file = event.target.files[0];if (!file) return;
  if (file.size > 4000000) return importReport('Invalid: workflow must be 4 MB or smaller.');
  try {
    importedWorkflow = JSON.parse(await file.text());
    if (!importedWorkflow || Array.isArray(importedWorkflow) || typeof importedWorkflow !== 'object') throw Error('Choose a workflow JSON object.');
    importReport('JSON syntax valid. Click Validate to check the H3 graph and rental dependencies.');
  } catch (error) { importedWorkflow=null;importReport(`Invalid JSON: ${error.message}`); }
});
document.querySelector('#inspect-profile').addEventListener('click', async () => {
  if (!importedWorkflow) return importReport('Choose a valid JSON file first.');
  document.querySelector('#save-import-profile').disabled=true;
  try {
    const result=await jsonRequest('/api/v1/setup/profiles/inspect',{method:'POST',body:JSON.stringify(importPayload())});
    importReport(JSON.stringify({valid:result.valid,rental_ready:result.ready,errors:result.errors,missing_models:result.unresolved_models,model_sources:result.analysis?.profile_draft,note:result.note},null,2));
    document.querySelector('#save-import-profile').disabled=!result.valid;
    document.querySelector('#import-model-help').hidden=!result.unresolved_models?.length;
    if(result.valid)window.dispatchEvent(new CustomEvent('commander:profile-workflow',{detail:importedWorkflow}));
  } catch(error){importReport(error.message);}
});
document.querySelector('#save-import-profile').addEventListener('click',async event=>{
  if(!importedWorkflow||!confirm('Save this reviewed H3 profile? Unresolved dependencies keep it as a draft; this does not rent a GPU or run the workflow.'))return;
  event.currentTarget.disabled=true;
  try{const result=await jsonRequest('/api/v1/setup/profiles',{method:'POST',body:JSON.stringify(importPayload())});importReport(result.ready?'Profile saved and available in rental profile choices.':'Draft saved. Resolve missing model sources and validate again before renting.');await loadImportedProfiles();}catch(error){importReport(error.message);}
});
async function loadImportedProfiles(){
  try{const data=await jsonRequest('/api/v1/setup/profiles');const root=document.querySelector('#import-saved-profiles');root.replaceChildren();for(const profile of data.profiles){const line=document.createElement('p');line.textContent=`${profile.name} · ${profile.ready?'Rental dependencies resolved':'Draft · dependencies unresolved'}`;root.append(line);}}catch{}
}
document.querySelector('#import-open-models').addEventListener('click',()=>{if(settingsMode)showStep(8);const panel=document.querySelector('#lora-management');panel.hidden=false;panel.scrollIntoView({behavior:'smooth'});});

async function loadCloudSsh(){
  try {
    const data=await jsonRequest('/api/v1/setup/cloud-ssh');
    document.querySelector('#cloud-ssh-status').textContent=data.ready?'SSH key pair found. Cloud SSH access is configured.':'SSH key pair missing. Complete this one-time host setup before renting.';
    document.querySelector('#cloud-ssh-command').textContent=data.ready?'':data.command;
    document.querySelector('#create-cloud-ssh').hidden=!data.can_create;
  } catch {document.querySelector('#cloud-ssh-status').textContent='Sign in to view SSH setup instructions.';}
}
document.querySelector('#refresh-cloud-ssh').addEventListener('click',loadCloudSsh);
loadCloudSsh();

async function loadPreferences() {
  const form=document.querySelector('#preferences-form');
  if(!form)return;
  const status=document.querySelector('#preferences-status');
  try {
    const data=await jsonRequest('/api/v1/settings/preferences');
    for(const [name,value] of Object.entries(data)) {
      const input=form.elements.namedItem(name);
      if(!input)continue;
      if(input.type==='checkbox')input.checked=value;else input.value=name==='quality_preset'&&value===null?(data.turbo_steps===8?'normal':'preview'):value;
    }
    status.textContent='Saved defaults loaded. Changes apply to fresh forms; existing drafts are kept.';
  } catch(error){status.textContent=error.message+' · Use the Sign in tab to unlock Settings.';}
}
async function submitPreferences(save) {
  const form=document.querySelector('#preferences-form'),status=document.querySelector('#preferences-status');
  if(!form.reportValidity())return;
  const payload={};
  for(const input of form.elements)if(input.name)payload[input.name]=input.type==='checkbox'?input.checked:['megapixels','duration','turbo_steps'].includes(input.name)?Number(input.value):input.value;
  const buttons=[...form.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try {
    await jsonRequest('/api/v1/settings/preferences'+(save?'':'/validate'),{method:save?'PUT':'POST',body:JSON.stringify(payload)});
    status.textContent=save?'Defaults validated and saved on this server. Open a fresh form to use them.':'Values are valid. Nothing has been saved.';
  } catch(error){status.textContent=typeof error.message==='string'?error.message:'Defaults could not be saved.';}
  finally{buttons.forEach(button=>button.disabled=false);}
}
document.querySelector('#preferences-form')?.addEventListener('submit',event=>{event.preventDefault();void submitPreferences(true);});
document.querySelector('#validate-preferences')?.addEventListener('click',()=>void submitPreferences(false));
document.querySelector('#reload-preferences')?.addEventListener('click',()=>void loadPreferences());
if(settingsMode)void loadPreferences();

document.querySelector('#show-password-change')?.addEventListener('click',()=>{
  document.querySelector('#change-password-form').hidden=false;
  document.querySelector('#password-recovery-panel').hidden=true;
});
document.querySelector('#show-password-recovery')?.addEventListener('click',async()=>{
  document.querySelector('#change-password-form').hidden=true;
  document.querySelector('#password-recovery-panel').hidden=false;
  const help=document.querySelector('#password-recovery-help');
  try {
    const data=await jsonRequest('/api/v1/cloud/password-recovery');
    document.querySelector('#recover-password-form').hidden=!data.available;
    document.querySelector('#recovery-key-file').textContent=data.recovery_key_file||'';
    help.textContent=data.available?'Use the recovery key stored on this host PC. Your password and recovery key are never displayed.':'On the computer running Commander, open http://127.0.0.1:8000/settings#access and choose Forgot password. Recovery is unavailable over phone/tailnet connections.';
  } catch(error){help.textContent=error.message;}
});
for(const [id,endpoint,credential] of [['change-password-form','password-change','current_password'],['recover-password-form','password-recover','recovery_key']]) {
  document.querySelector('#'+id)?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget,status=form.querySelector('[role="status"]'),button=event.submitter;
    if(form.elements.new_password.value!==form.elements.confirm_password.value){status.textContent='The new passwords do not match. Nothing changed.';return;}
    button.disabled=true;status.textContent='Updating password…';
    try {
      const data=await jsonRequest('/api/v1/cloud/'+endpoint,{method:'POST',body:JSON.stringify({[credential]:form.elements[credential].value,new_password:form.elements.new_password.value})});
      form.reset();status.textContent=data.message;
      const signIn=document.querySelector('#password-form');signIn.reset();signIn.querySelector('button').disabled=false;signIn.querySelector('button').textContent='Sign in';
    } catch(error){status.textContent=error.message;}
    finally{button.disabled=false;}
  });
}

document.querySelector('#create-cloud-ssh')?.addEventListener('click',async event=>{
  if(!confirm('Create a dedicated SSH key on this PC? Its private file stays here and is restricted to your local account. Commander uses it to connect to cloud workers. This does not rent a GPU or replace existing keys.'))return;
  const button=event.currentTarget;button.disabled=true;
  try{await jsonRequest('/api/v1/setup/cloud-ssh',{method:'POST',body:JSON.stringify({confirm:true})});await loadCloudSsh();}
  catch(error){document.querySelector('#cloud-ssh-status').textContent=error.message;}
  finally{button.disabled=false;}
});
