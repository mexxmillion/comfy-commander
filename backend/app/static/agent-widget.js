(() => {
  const state = {messages: [], workflow: null};
  const launcher = document.createElement('button');
  launcher.className = 'tm-agent-launcher';
  launcher.type = 'button';
  launcher.textContent = 'AI';
  launcher.setAttribute('aria-label', 'Open Tensor Master copilot');
  const panel = document.createElement('section');
  panel.className = 'tm-agent';
  panel.hidden = true;
  panel.innerHTML = `
    <header><div><strong>Tensor Master Copilot</strong><small>BYOK · actions require confirmation</small></div><button class="tm-agent-close" type="button" aria-label="Close">×</button></header>
    <div class="tm-agent-controls"><select aria-label="Agent model"><option value="deepseek/deepseek-v4.1-flash-1017">DeepSeek V4.1 Flash</option><option value="google/gemini-3.8-flash">Gemini 3.8 Flash</option></select><label>Attach workflow JSON<input type="file" accept="application/json,.json"></label></div>
    <div class="tm-agent-messages" aria-live="polite"><div class="tm-agent-message">Tell me what you want to run, attach a ComfyUI workflow, or ask me to help configure a cloud profile.</div></div>
    <form class="tm-agent-compose"><textarea aria-label="Message" placeholder="e.g. inspect this workflow and prepare it for a cloud worker" required></textarea><button type="submit">Send</button></form>`;
  document.body.append(panel, launcher);
  const messages = panel.querySelector('.tm-agent-messages');
  const textarea = panel.querySelector('textarea');
  const send = panel.querySelector('.tm-agent-compose button');

  const addMessage = (content, role, detail) => {
    const item = document.createElement('div');
    item.className = `tm-agent-message ${role}`;
    item.textContent = content;
    if (detail) {
      const small = document.createElement('small');
      small.textContent = detail;
      item.appendChild(small);
    }
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
  };

  const runAction = async (action, button) => {
    button.disabled = true;
    if (action.type === 'open_setup') {
      location.href = '/setup#lora-management';
      return;
    }
    if (action.type === 'open_rental') {
      if (!document.querySelector('#rental-profile')) {
        location.href = '/#rentals';
        return;
      }
      const profile = document.querySelector('#rental-profile');
      profile.value = action.payload.profile;
      profile.dispatchEvent(new Event('change'));
      location.hash = 'rentals';
      panel.hidden = true;
      button.disabled = false;
      return;
    }
    if (action.type === 'submit_workflow') {
      try {
        const response = await fetch('/api/v1/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(action.payload)});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Workflow was rejected');
        addMessage(`Queued job ${data.job_id}. I’ll leave validation and worker routing visible in the main studio.`, 'assistant');
      } catch (error) {
        addMessage(error.message, 'assistant');
        button.disabled = false;
      }
    }
  };

  const addAction = action => {
    if (!action) return;
    const card = document.createElement('div');
    card.className = 'tm-agent-action';
    const warning = document.createElement('span');
    warning.textContent = action.warning || 'This action requires your confirmation.';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = action.label;
    button.addEventListener('click', () => runAction(action, button));
    card.append(warning, button);
    messages.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  };

  launcher.addEventListener('click', () => { panel.hidden = !panel.hidden; if (!panel.hidden) textarea.focus(); });
  panel.querySelector('.tm-agent-close').addEventListener('click', () => { panel.hidden = true; });
  panel.querySelector('input[type="file"]').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 4_000_000) return addMessage('Workflow JSON must be 4 MB or smaller.', 'assistant');
    try {
      state.workflow = JSON.parse(await file.text());
      addMessage(`Attached ${file.name}. I’ll inspect node types and model references without executing it.`, 'assistant');
    } catch {
      addMessage('That file is not valid JSON.', 'assistant');
    }
  });
  panel.querySelector('form').addEventListener('submit', async event => {
    event.preventDefault();
    const content = textarea.value.trim();
    if (!content) return;
    state.messages.push({role: 'user', content});
    addMessage(content, 'user');
    textarea.value = '';
    send.disabled = true;
    try {
      const response = await fetch('/api/v1/agent/chat', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: panel.querySelector('select').value, messages: state.messages.slice(-12), workflow: state.workflow})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Copilot request failed');
      state.messages.push({role: 'assistant', content: data.message});
      const detail = data.workflow_analysis ? `${data.workflow_analysis.node_count} nodes · ${data.workflow_analysis.model_references.length} model references found` : null;
      addMessage(data.message, 'assistant', detail);
      addAction(data.action);
    } catch (error) {
      addMessage(error.message, 'assistant', 'Sign in and connect OpenRouter in Setup & Models if needed.');
    } finally {
      send.disabled = false;
      textarea.focus();
    }
  });
})();
