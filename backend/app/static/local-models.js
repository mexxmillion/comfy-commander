// User-driven local provisioning. Never download on navigation or profile change.
(() => {
  const $ = selector => document.querySelector(selector);
  const form = $('#local-model-form');
  if (!form) return;
  let inventory = null, selection = null, timer = null, busy = false, checkVersion = 0;
  const status = message => { $('#local-model-status').textContent = message; };
  const payload = () => ({root: $('#local-comfy-root').value.trim(), model_set: $('#local-model-set').value});
  const enable = () => {
    $('#download-local-models').disabled = busy || !inventory || !inventory.missing_bytes || inventory.files.some(row => row.status === 'conflict') || !$('#local-download-consent').checked;
  };
  const clear = () => { inventory = null; $('#local-model-report').hidden = true; $('#local-download-consent').checked = false; enable(); };
  const line = (target, text, url, linkText) => {
    const row = document.createElement('p'); row.textContent = text + ' ';
    if (url) { const link = document.createElement('a'); link.href = url; link.textContent = linkText; link.target = '_blank'; link.rel = 'noopener'; row.append(link); }
    target.append(row);
  };
  async function check() {
    const version = ++checkVersion;
    clear(); status('Checking installation, files and loaded nodes…');
    try {
      selection = payload();
      const data = await jsonRequest('/api/v1/setup/local-models/check', {method:'POST', body:JSON.stringify(selection)});
      if (version !== checkVersion) return;
      inventory = data; $('#local-comfy-root').value = data.root;
      $('#local-model-report').hidden = false;
      $('#local-model-summary').textContent = `${data.files.filter(row=>row.status==='found').length}/${data.files.length} files found · ${formatBytes(data.missing_bytes)} to download · ${formatBytes(data.free_bytes)} free. ${data.note}`;
      const files = $('#local-model-files'); files.replaceChildren();
      for (const row of data.files) {
        line(files, `${row.status === 'found' ? 'Found (size matches)' : row.status === 'conflict' ? 'Conflict · review existing file' : 'Missing'} · ${formatBytes(row.bytes)} · ${row.destination}`, row.url, 'Download file');
        line(files, '', row.repository_url, 'Repository & license');
      }
      $('#local-node-status').textContent = `${data.runtime.status === 'nodes_loaded' ? 'Required node classes loaded' : data.runtime.status === 'missing_nodes' ? 'Missing loaded nodes: '+data.runtime.missing_classes.join(', ') : 'ComfyUI is not responding'}. ${data.runtime.note}`;
      const nodes = $('#local-node-list'); nodes.replaceChildren();
      for (const node of data.nodes) line(nodes, `${node.found?'Folder found':'Folder missing'} · ${node.name} · ${node.note}${node.revision?' · '+node.revision:''}`, node.url, 'Source');
      $('#local-model-report details').open = data.runtime.status !== 'nodes_loaded' || data.nodes.some(node=>!node.found);
      status(data.missing_bytes ? 'Review the missing files below. No downloads started.' : 'Model files found. Review node and worker checks before generating.');
      enable();
    } catch(error) { status(error.message); }
  }
  async function poll() {
    try {
      const data = await jsonRequest('/api/v1/setup/local-models');
      busy = data.download.status === 'downloading'; enable();
      if (busy) {
        status(`Downloading models · ${data.download.completed_files}/${data.download.total_files} verified · ${data.download.current_file || 'Starting…'}. Progress updates per file; large files may take several minutes.`);
        timer = setTimeout(poll, 2000);
      } else if (data.download.status === 'completed') {
        await check(); status('Download completed · new files passed SHA-256 verification. Restart ComfyUI if needed, then check nodes and connect your local worker.');
      } else if (data.download.status === 'failed') status(data.download.error);
    } catch(error) { status(error.message+' Recheck after reconnecting; do not start a duplicate download.'); }
  }
  async function load() {
    try {
      const data = await jsonRequest('/api/v1/setup/local-models');
      $('#local-comfy-root').value = data.root;
      const select = $('#local-model-set'); select.replaceChildren();
      for (const item of data.sets) { const option = document.createElement('option'); option.value=item.id; option.textContent=item.label; select.append(option); }
      select.value = data.model_set;
      if (data.download.status === 'downloading') { clearTimeout(timer); await poll(); }
      else if (data.root) await check();
      else status('Select your ComfyUI folder to check models and custom nodes. If it is not installed, use Download ComfyUI above.');
    } catch(error) { status(error.message); }
  }
  form.addEventListener('submit', event=>{event.preventDefault();void check();});
  $('#local-comfy-root').addEventListener('input',()=>{checkVersion++;clear();});
  $('#local-model-set').addEventListener('change',()=>{clear();if($('#local-comfy-root').value)void check();});
  $('#local-download-consent').addEventListener('change', enable);
  $('#download-local-models').addEventListener('click', async()=>{
    busy=true;enable();status('Starting the reviewed model download…');
    try { await jsonRequest('/api/v1/setup/local-models/download',{method:'POST',body:JSON.stringify(selection)});await poll(); }
    catch(error) {busy=false;enable();status(error.message);}
  });
  window.addEventListener('commander-local-setup',()=>{ $('#local-model-setup').scrollIntoView({behavior:'smooth',block:'start'});void load(); });
  window.addEventListener('commander-local-auth-ready',()=>void load());
  void load();
})();
