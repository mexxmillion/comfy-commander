const jsonRequest = async (url, options = {}) => {
  const response = await fetch(url, {credentials: 'same-origin', headers: {'Content-Type': 'application/json', ...(options.headers || {})}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed');
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
  railButtons.forEach((button, i) => { button.classList.toggle('active', i === currentStep); button.classList.toggle('complete', i < currentStep); });
  if (currentStep === 4) renderReadiness();
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
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
});

if (document.body.dataset.firstRun === 'false') document.querySelector('#protect-next').disabled = false;
document.querySelector('#manage-models')?.addEventListener('click', () => {
  document.querySelectorAll('.advanced-section').forEach(section => section.hidden = false);
  document.querySelector('#lora-management').scrollIntoView({behavior: 'smooth'});
});
showStep(0);

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
