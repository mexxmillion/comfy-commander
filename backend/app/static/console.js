/* Read-only monitor interactions. Rental mutations remain in the guarded UI. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  let state = JSON.parse($('#console-state').textContent);
  let selected = state.tasks[0]?.id, paused = false, refreshing = false, tab = 'overview', taskPage = 0, outputPage = 0, durableOutputs = null;
  let refreshPending = false;
  const checkedTasks = new Set();
  const checkedOutputs = new Set();
  let shownOutputs = [], removingOutputs = false, outputLoad = 0;
  const inspector = $('#job-inspector'), viewBase = inspector.dataset.viewBase;
  const logPanel = $('#activity');
  $('main').append(logPanel);
  // Navigation must remain available even if an optional empty-state control is absent.
  // A fresh install has no task rows, so initialize routes before binding queue controls.
  addEventListener('hashchange',nav);nav();
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const seconds = value => value == null ? '—' : `${Number(value).toFixed(1)} s`;
  const date = value => value ? new Date(value).toLocaleString() : '—';
  const selectedTask = () => state.tasks.find(task => task.id === selected);
  function matchingTasks() {
    const term = $('#task-search').value.toLowerCase(), status = $('#task-filter').value;
    const result = state.tasks.filter(t => (status === 'all' || t.status === status) &&
      [t.job_name,t.id,t.worker_id,t.runtime,t.message].join(' ').toLowerCase().includes(term));
    const mode = $('#task-sort').value;
    // Run order: queued tasks by rank (#1 runs next), then everything else newest first.
    const rankOf = t => t.status === 'queued' && t.rank ? t.rank : Infinity;
    if (mode === 'rank') return result.sort((a,b) => rankOf(a) - rankOf(b) || b.created_at.localeCompare(a.created_at));
    return result.sort((a,b) => mode === 'name' ? a.job_name.localeCompare(b.job_name) : mode === 'status' ? a.status.localeCompare(b.status) : mode === 'oldest' ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at));
  }
  function tasks() {
    const matches=matchingTasks(), size=Number($('#task-page-size').value)||25;
    const pages=Math.max(1,Math.ceil(matches.length/size));taskPage=Math.min(taskPage,pages-1);
    return matches.slice(taskPage*size,(taskPage+1)*size);
  }
  // Priority: higher number is claimed sooner (100 = medium, 1 = lowest).
  async function changePriority(taskId, body) {
    const response=await fetch(`/api/v1/operator/tasks/${encodeURIComponent(taskId)}/priority`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let result={};try{result=await response.json();}catch{}
    if(!response.ok){window.alert(result.detail||'Could not change task priority. Sign in under Rentals if required.');await refresh();return;}
    $('#task-sort').value='rank';taskPage=0;selected=taskId;
    await refresh();
  }
  const routeEligible = task => task && !['running','cancel_requested'].includes(task.status);
  let removingTasks = false;
  async function removeQueueEntries(ids) {
    if(removingTasks||!ids.length)return;
    if(!window.confirm(`Remove ${ids.length} queue entr${ids.length===1?'y':'ies'}? Waiting tasks will be cancelled. Saved outputs and media files are kept.`))return;
    removingTasks=true;const errors=[];
    try {
      for(const id of ids){
        try {
          const response=await fetch(`/api/v1/operator/tasks/${encodeURIComponent(id)}/remove`,{method:'POST'});
          const body=await response.json();
          if(!response.ok)throw Error(body.detail||'Could not remove entry');
          checkedTasks.delete(id);if(selected===id)selected=null;
        } catch(error){errors.push(`${id}: ${error.message}`);}
      }
      await refresh();
      if(errors.length)window.alert(`Some entries could not be removed:\n${errors.join('\n')}`);
    } finally {removingTasks=false;}
  }
  function updateBulkSelection() {
    const known=new Set(state.tasks.map(task=>task.id));
    for(const id of checkedTasks)if(!known.has(id))checkedTasks.delete(id);
    document.querySelectorAll('.task-check').forEach(box=>{box.checked=checkedTasks.has(box.closest('[data-task-id]').dataset.taskId);});
    const bar=$('#bulk-actions'),count=checkedTasks.size;
    bar.hidden=count===0;$('#bulk-count').textContent=`${count} selected`;
    const visible=tasks().filter(routeEligible),selectedVisible=visible.filter(task=>checkedTasks.has(task.id));
    const master=$('#select-visible-tasks');
    if(master){master.checked=visible.length>0&&selectedVisible.length===visible.length;master.indeterminate=selectedVisible.length>0&&selectedVisible.length<visible.length;}
  }
  function syncBulkWorkers() {
    const select=$('#bulk-worker'),value=select.value;
    select.replaceChildren(Object.assign(el('option','Automatic · first compatible worker'),{value:''}));
    for(const item of state.workers){const option=el('option',`${item.name} · ${item.gpu} · ${item.status}`);option.value=item.id;select.append(option);}
    select.value=[...select.options].some(option=>option.value===value)?value:'';
  }
  function filterTasks() {
    const matches=matchingTasks(), size=Number($('#task-page-size').value)||25;
    const pages=Math.max(1,Math.ceil(matches.length/size));taskPage=Math.max(0,Math.min(taskPage,pages-1));
    const visible=matches.slice(taskPage*size,(taskPage+1)*size), ids = new Set(visible.map(t=>t.id));
    document.querySelectorAll('[data-task-id]').forEach(row => { row.hidden = !ids.has(row.dataset.taskId); row.classList.toggle('selected', row.dataset.taskId === selected); row.setAttribute('aria-selected', String(row.dataset.taskId === selected)); });
    const body = $('#tasks tbody'), mobile = $('.mobile-task-list');
    const rows = [...document.querySelectorAll('[data-task-id]')];
    for (const container of [body, mobile]) {
      if (!container) continue;
      const ordered = visible.map(task => rows.find(row => row.parentElement === container && row.dataset.taskId === task.id));
      const current = [...container.children].filter(row => !row.hidden);
      if (ordered.some((row, index) => row !== current[index])) for (const row of ordered) if (row) container.append(row);
    }
    const first=matches.length?taskPage*size+1:0,last=Math.min((taskPage+1)*size,matches.length);
    $('#filtered-count').textContent = `${first}–${last} of ${matches.length} matching · ${state.metrics.total_tasks} total`;
    $('#task-page-label').textContent=`Page ${taskPage+1} of ${pages}`;
    $('#task-prev').disabled=taskPage===0;$('#task-next').disabled=taskPage>=pages-1;
    updateBulkSelection();
  }
  function filterWorkers() {
    const term = $('#worker-search').value.toLowerCase();
    document.querySelectorAll('.worker-table tbody tr,.mobile-worker-list article').forEach(row => row.hidden = !row.textContent.toLowerCase().includes(term));
  }
  function visibleEvents() {
    const term = $('#log-search').value.toLowerCase(), kind = $('#log-filter').value;
    return state.events.filter(e => (kind==='all'||e.kind===kind) && e.message.toLowerCase().includes(term) && (!$('#selected-logs').checked || (selected && e.message.includes(selected))));
  }
  function renderLogs() {
    const stream = $('.log-stream'), scroll = stream.scrollTop, fragment = document.createDocumentFragment();
    for (const event of visibleEvents()) {
      const row = el('div'); row.dataset.kind = event.kind;
      const time = el('time', new Date(event.timestamp).toLocaleTimeString()); time.title=date(event.timestamp);
      row.append(time, el('span',event.kind,'log-kind'),el('p',event.message)); fragment.append(row);
    }
    if (!fragment.childNodes.length) fragment.append(el('p','No matching events in the latest 200.','inspect-note'));
    stream.replaceChildren(fragment); stream.scrollTop = scroll;
  }
  function outputUrl(artifact) {
    if (!artifact) return null;
    const raw = artifact.url || `${viewBase}?${new URLSearchParams({filename:artifact.filename||'',subfolder:artifact.subfolder||'',type:artifact.type||'output'})}`;
    try {
      const url = new URL(raw,location.href);
      if (url.pathname === '/view')
        return `/api/v1/media/file${url.search}`;
      if (url.pathname.startsWith('/h3-mobile/dispatch-output/')) return url.pathname.replace('/h3-mobile/dispatch-output/', '/outputs/');
      return ['http:','https:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }
  const liveMarkup = new WeakMap();
  function releaseMedia(root) {
    for (const media of root.querySelectorAll('video,audio')) {
      media.pause();
      media.removeAttribute('src');
      media.load();
    }
  }
  function parseLiveDashboard(markup) {
    // Template contents are inert: polling must not create another document
    // that loads historical videos/images before we extract the live tables.
    const template = document.createElement('template');
    template.innerHTML = markup;
    return template.content;
  }
  function syncLiveRegion(target, fresh) {
    const markup = fresh.innerHTML;
    if (liveMarkup.get(target) === markup) return false;
    releaseMedia(target);
    target.innerHTML = markup;
    liveMarkup.set(target, markup);
    return true;
  }
  const specs = new Map();
  // Render settings are fetched once per job; the queue snapshot stays small.
  function jobSpec(task) {
    if (specs.has(task.job_id)) return specs.get(task.job_id);
    specs.set(task.job_id, null);
    fetch(`/api/v1/tasks/${encodeURIComponent(task.id)}/spec`).then(r => r.ok ? r.json() : {error: 'Settings unavailable'})
      .catch(() => ({error: 'Settings unavailable'}))
      .then(spec => { specs.set(task.job_id, spec); if (selected === task.id) renderInspector(true); });
    return null;
  }
  function specSection(spec) {
    const section = el('section', null, 'job-spec');
    section.append(el('h3', 'Job spec', 'inspect-title'));
    if (!spec) { section.append(el('p', 'Loading render settings…', 'inspect-note')); return section; }
    if (spec.error) { section.append(el('p', spec.error, 'inspect-note')); return section; }
    const name = value => String(value || '').split(/[\\/]/).pop();
    const chips = el('div', null, 'spec-chips');
    const pixels = spec.width && spec.height ? `${spec.width} × ${spec.height} px` : null;
    const size = pixels || spec.size_note;
    const length = spec.frames === 1 ? 'Still frame' : spec.duration_seconds ? `${spec.duration_seconds}s · ${spec.frames} frames @ ${spec.fps}fps` : null;
    for (const value of [spec.type, size, length, spec.steps != null && `${spec.steps} steps · ${spec.sampler || 'sampler ?'}${spec.cfg != null ? ` · CFG ${spec.cfg}` : ''}`, spec.aspect_label, spec.megapixels && `${spec.megapixels} MP`, spec.seed != null && `Seed ${spec.seed}`])
      if (value) chips.append(el('span', value));
    section.append(chips);
    const prompt = el('div', null, 'spec-prompt');
    prompt.append(el('strong', spec.creative ? 'Creative concept' : 'Your prompt'), el('p', spec.prompt || 'No prompt recorded'));
    section.append(prompt);
    if (spec.ai_prompt) {
      const ai = el('details', null, 'spec-ai'); ai.append(el('summary', `AI enhanced prompt${spec.ai_model ? ` · ${spec.ai_model}` : ''}`), el('pre', spec.ai_prompt)); section.append(ai);
    } else if (spec.enhance === false) section.append(el('p', 'AI enhancement off · prompt used exactly as written', 'inspect-note'));
    const grid = el('dl', null, 'inspect-grid');
    grid.append(el('dt', 'Resolution'), el('dd', pixels || spec.size_note || 'Not recorded'));
    if(spec.requested_duration_seconds != null)grid.append(el('dt','Requested duration'),el('dd',`${spec.requested_duration_seconds}s → ${spec.duration_seconds}s on H3's frame grid`));
    if(spec.encoded_aspect)grid.append(el('dt','Aspect ratio'),el('dd',`Requested ${spec.aspect?.split(' ')[0] || 'graph dimensions'} · encoded ${spec.encoded_aspect} (aligned pixels)`));
    if(spec.video_preparation)grid.append(el('dt','Reference preparation'),el('dd',spec.video_preparation));
    if(spec.frame_note)grid.append(el('dt','Frame count'),el('dd',`${spec.frames ?? 'Unknown'} output frames · ${spec.frame_note}`));
    if (spec.scheduler) grid.append(el('dt', 'Scheduler'), el('dd', spec.scheduler));
    const order = ['Diffusion', 'Text encoder', 'VAE'];
    for (const model of [...spec.models].sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role))) grid.append(el('dt', model.role), el('dd', name(model.name)));
    grid.append(el('dt', 'LoRAs'), el('dd', spec.loras.length ? spec.loras.map(l => `${name(l.name)} @ ${l.sparsity === undefined ? l.strength : `${l.sparsity} sparsity`}`).join('\n') : 'None'));
    grid.append(el('dt', 'References'), el('dd', spec.references?.length ? spec.references.map(r => `${r.kind} · ${name(r.file)}`).join('\n') : 'None (text only)'));
    if (spec.render_target) grid.append(el('dt', 'Render target'), el('dd', spec.render_target));
    section.append(grid);
    return section;
  }
  function renderInspector(force = false) {
    const task = selectedTask();
    if (!task) { releaseMedia(inspector); inspector.replaceChildren(el('h2','Task inspector'),el('p','Select a task to view its status, timings, output and events.','inspect-note')); return; }
    const signature = JSON.stringify(task) + tab;
    if (!force && inspector.dataset.signature === signature) return;
    inspector.dataset.signature = signature;
    const oldMedia = inspector.querySelector('video,img,audio');
    const oldUrl = oldMedia?.getAttribute('src');
    const head = el('div',null,'panel-head'); head.append(el('h2','TASK INSPECTOR'), el('span',task.status,`state ${task.status}`));
    const tabs = el('div',null,'inspect-tabs');
    for (const name of ['overview','data']) { const button=el('button',name==='overview'?'Overview / output':'Raw metadata',name===tab?'active':''); button.type='button'; button.onclick=()=>{tab=name;renderInspector(true);}; tabs.append(button); }
    const fragment=document.createDocumentFragment(); fragment.append(head,el('h3',task.job_name,'inspect-title'),tabs);
    const edit=el('button','Edit & rerun');edit.type='button';
    edit.onclick=()=>{
      openGeneratorJob(task.job_id);
    };
    const actions=el('div',null,'inspect-actions');actions.append(edit);
    const operate=async action=>{
      const labels={requeue:'Requeue this task?',cancel:task.status==='running'?'Interrupt this running task?':'Cancel this queued task?', 'restart-worker':'Restart this task’s local GPU worker and requeue the saved job? The other GPU is left running.'};
      if(action!=='restart-worker'&&!window.confirm(labels[action]))return;
      const response=await fetch(`/api/v1/operator/tasks/${encodeURIComponent(task.id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'}});
      let body={};try{body=await response.json();}catch{}
      if(!response.ok){window.alert(body.detail||'Task action failed. Sign in under Rentals if required.');return;}
      await refresh();
    };
    if(['failed','cancelled','completed'].includes(task.status)){const retry=el('button','Requeue task','primary');retry.type='button';retry.onclick=()=>operate('requeue');actions.append(retry);}
    if(['failed','cancelled'].includes(task.status)&&['local-3090','local-3090-gpu2'].includes(task.worker_id)){const recover=el('button','Restart worker & requeue');recover.type='button';recover.onclick=()=>operate('restart-worker');actions.append(recover);}
    if(['queued','running','cancel_requested'].includes(task.status)){const cancel=el('button',task.status==='queued'?'Cancel task':'Kill / interrupt','danger');cancel.type='button';cancel.disabled=task.status==='cancel_requested';cancel.onclick=()=>operate('cancel');actions.append(cancel);}
    const remove=el('button','Remove queue entry','danger');remove.type='button';remove.disabled=!routeEligible(task);remove.title=remove.disabled?'Stop this task before removing its queue entry':'Remove entry; keep saved outputs';remove.onclick=()=>removeQueueEntries([task.id]);actions.append(remove);
    fragment.append(actions);
    if(!['running','cancel_requested'].includes(task.status)){
      const queued=task.status==='queued';
      const editor=el('section',null,'priority-editor'),head=el('div',null,'priority-editor-head');
      head.append(el('h3','Rank · priority'),el('span',queued&&task.rank?`#${task.rank} in queue`:'Not queued',`rank-badge${queued?'':' idle'}`));
      const row=el('div',null,'priority-editor-row');
      const number=el('input');number.type='number';number.inputMode='numeric';number.min='1';number.max='10000';number.step='1';number.value=String(task.priority??100);number.setAttribute('aria-label','Task priority');
      const save=el('button','Set priority','primary');save.type='button';
      const apply=()=>{const value=Math.round(Number(number.value));if(!Number.isFinite(value)||value<1||value>10000){window.alert('Priority must be a whole number from 1 to 10000.');return;}changePriority(task.id,{priority:value});};
      save.onclick=apply;number.onkeydown=event=>{if(event.key==='Enter')apply();};
      row.append(number,save);
      const moves=el('div',null,'prio-moves');
      for(const [move,label] of [['top','⤒ Top'],['up','▲ Up'],['down','▼ Down'],['bottom','⤓ Bottom']]){const button=el('button',label);button.type='button';button.disabled=!queued;button.title=queued?`Move ${move==='top'||move==='bottom'?'to '+move:move+' one'}`:'Only queued tasks can be re-ranked';button.onclick=()=>changePriority(task.id,{move});moves.append(button);}
      editor.append(head,row,moves,el('p','Higher number runs sooner · 100 = medium · 1 = lowest · ties run oldest first.','inspect-note'));
      fragment.append(editor);
    }
    if(!['running','cancel_requested'].includes(task.status)){
      const route=el('section',null,'route-editor'),title=el('h3','Dispatch route');
      const pool=el('select');pool.setAttribute('aria-label','Worker pool');
      for(const [value,label] of [['any','Any available · local + cloud'],['local','Local pool only'],['standard','Cloud pool only']]){const option=el('option',label);option.value=value;option.selected=task.policy===value;pool.append(option);}
      const worker=el('select');worker.setAttribute('aria-label','Preferred worker');
      const automatic=el('option','Automatic · first compatible worker');automatic.value='';worker.append(automatic);
      for(const item of state.workers){const option=el('option',`${item.name} · ${item.gpu} · ${item.status}`);option.value=item.id;option.selected=task.preferred_worker_id===item.id;worker.append(option);}
      if(task.preferred_worker_id&&!state.workers.some(item=>item.id===task.preferred_worker_id)){const offline=el('option',`${task.preferred_worker_id} · offline`);offline.value=task.preferred_worker_id;offline.selected=true;worker.append(offline);}
      const vram=el('input');vram.type='number';vram.min='1';vram.max='192';vram.value=String(task.min_vram_gb||16);vram.setAttribute('aria-label','Minimum VRAM GB');
      const architecture=el('select');architecture.setAttribute('aria-label','GPU architecture');
      for(const value of ['any','ampere','ada','blackwell']){const option=el('option',value==='any'?'Any architecture':value[0].toUpperCase()+value.slice(1));option.value=value;option.selected=(task.architecture||'any')===value;architecture.append(option);}
      const fields=el('div',null,'route-fields');
      const field=(label,input)=>{const wrap=el('label');wrap.append(el('span',label),input);return wrap;};
      fields.append(field('Worker pool',pool),field('Preferred worker',worker),field('Minimum VRAM (GB)',vram),field('Architecture',architecture));
      const terminal=['failed','cancelled','completed'].includes(task.status);
      const apply=el('button',terminal?'Save route & requeue':'Apply route','primary');apply.type='button';
      apply.onclick=async()=>{
        const action=terminal?'change this route and requeue the task':'change this queued task route';
        if(!window.confirm(`Are you sure you want to ${action}?`))return;
        const response=await fetch(`/api/v1/operator/tasks/${encodeURIComponent(task.id)}/route`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pool:pool.value,preferred_worker_id:worker.value||null,min_vram_gb:Number(vram.value),architecture:architecture.value,requeue:terminal})});
        let body={};try{body=await response.json();}catch{}
        if(!response.ok){window.alert(body.detail||'Could not change task route. Sign in under Rentals if required.');return;}
        await refresh();
      };
      route.append(title,fields,apply,el('p',task.preferred_worker_id?'Pinned tasks wait for that exact worker. Clear the preference to restore automatic scheduling.':'Automatic scheduling sends this task to the first compatible worker in the selected pool.','inspect-note'));
      fragment.append(route);
    } else fragment.append(el('p','Stop the running task before changing its worker pool or preferred worker.','inspect-note'));
    if (tab==='data') fragment.append(el('pre',JSON.stringify({task,spec:jobSpec(task)},null,2),'raw-details'));
    else {
      fragment.append(specSection(jobSpec(task)));
      const grid=el('dl',null,'inspect-grid');
      for (const [key,value] of [['Task',task.id],['Priority',`${task.priority??100}${task.status==='queued'&&task.rank?` · rank #${task.rank}`:''}`],['Worker',task.worker_id||'Unassigned'],['Submitted',date(task.created_at)],['Runtime',task.runtime],['Policy',task.policy],['Requirement',task.requirement],['Attempt',task.attempt],['Progress',`${task.progress}%`],['Prompt ID',task.prompt_id||'—']]) grid.append(el('dt',key),el('dd',String(value)));
      fragment.append(grid,el('div',task.message||'No worker message yet.','inspect-message'));
      const timing=task.artifact?.timings||{}, boxes=el('div',null,'timing-list');
      for (const [label,key] of [['Queue','queue_seconds'],['Generation','generation_seconds'],['Before sampling','before_sampling_seconds'],[timing.sampling_scope==='sampler_nodes'?'Sampling':'Progress window (legacy)','sampling_seconds'],['After sampling','after_sampling_seconds']]) {const box=el('div');box.append(el('span',label),el('strong',seconds(timing[key])));boxes.append(box);}
      fragment.append(el('h3','Execution timings','inspect-title'),boxes);
      if(timing.recovery_seconds!=null){const recovery=el('div');recovery.append(el('span','Interrupted attempt / recovery'),el('strong',seconds(timing.recovery_seconds)));boxes.append(recovery);}
      const url=outputUrl(task.artifact), output=el('div',null,'inspect-artifact');
      if (url) {
        const kind=task.artifact.kind;
        const mediaTag=['videos','gifs'].includes(kind)?'video':kind==='images'?'img':kind==='audio'?'audio':null;
        if (mediaTag) { const media=oldUrl===url&&oldMedia?.tagName.toLowerCase()===mediaTag?oldMedia:el(mediaTag,null,'result-media'); if(media.getAttribute('src')!==url)media.src=url; if(mediaTag==='img') media.alt=task.job_name;else {media.controls=true;media.preload='none';} output.append(media); }
        const link=el('a',`Download · ${task.artifact.filename}`); link.href=url;link.download=task.artifact.filename;output.append(link);
      } else output.append(el('p','No output available for this task.','inspect-note'));
      fragment.append(output,el('p','— = not recorded. Generation uses runtime execution timestamps when available. Sampling phases are unavailable across a runtime restart.','inspect-note'));
    }
    releaseMedia(inspector);
    inspector.replaceChildren(fragment);
  }
  function preservePosition() {
    const x=window.scrollX, y=window.scrollY;
    const positions=[...document.querySelectorAll('#tasks .table-wrap, #tasks .mobile-task-list, #job-inspector')].map(node=>[node,node.scrollLeft,node.scrollTop]);
    return () => { for (const [node,left,top] of positions) { node.scrollLeft=left;node.scrollTop=top; } window.scrollTo({left:x,top:y,behavior:'instant'}); };
  }
  function choose(id) {
    const restore=preservePosition();
    selected=id;
    document.querySelectorAll('[data-task-id]').forEach(row=>{const active=row.dataset.taskId===id;row.classList.toggle('selected',active);row.setAttribute('aria-selected',String(active));});
    renderInspector(true);renderLogs();restore();
  }
  function focusTask(row, reveal=false) {
    row.focus({preventScroll:true});
    // Scroll only the queue, never the page or the inspector.
    const container=row.closest('.table-wrap');
    if (reveal && container) {
      const bounds=container.getBoundingClientRect(), rect=row.getBoundingClientRect();
      const top=bounds.top+(container.querySelector('thead')?.getBoundingClientRect().height||0);
      if(rect.top<top)container.scrollTop+=rect.top-top;
      else if(rect.bottom>bounds.bottom)container.scrollTop+=rect.bottom-bounds.bottom;
    }
  }
  function onTaskKeydown(event) {
    const row=event.target.closest('[data-task-id]');
    if(!row || event.target.closest('input,textarea,select,button,a,[contenteditable="true"]') || event.altKey || event.ctrlKey || event.metaKey)return;
    if(['Enter',' '].includes(event.key)){event.preventDefault();choose(row.dataset.taskId);return;}
    if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    const visible=tasks(), index=visible.findIndex(task=>task.id===row.dataset.taskId);
    if(index<0)return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?visible.length-1:Math.max(0,Math.min(visible.length-1,index+(event.key==='ArrowDown'?1:-1)));
    const target=[...row.parentElement.children].find(item=>item.dataset.taskId===visible[next].id);
    if(target){choose(visible[next].id);focusTask(target,true);}
  }
  $('#tasks').addEventListener('click',event=>{const row=event.target.closest('[data-task-id]');if(row&&!event.target.closest('a,button,input,select,textarea')){choose(row.dataset.taskId);focusTask(row);}});
  $('#tasks').addEventListener('change',event=>{if(!event.target.classList.contains('task-check'))return;const id=event.target.closest('[data-task-id]').dataset.taskId;if(event.target.checked)checkedTasks.add(id);else checkedTasks.delete(id);updateBulkSelection();});
  $('#tasks').addEventListener('keydown',onTaskKeydown);
  $('#tasks').addEventListener('click',event=>{const button=event.target.closest('[data-priority-move]');if(!button)return;const row=button.closest('[data-task-id]');button.disabled=true;changePriority(row.dataset.taskId,{move:button.dataset.priorityMove});});
  $('#tasks').addEventListener('change',event=>{if(!event.target.classList.contains('priority-input'))return;const value=Math.round(Number(event.target.value));if(!Number.isFinite(value)||value<1||value>10000){window.alert('Priority must be a whole number from 1 to 10000.');return;}changePriority(event.target.closest('[data-task-id]').dataset.taskId,{priority:value});});
  for(const id of ['task-search','task-filter','task-sort']) $('#'+id).addEventListener('input',()=>{taskPage=0;filterTasks();});
  $('#task-page-size').addEventListener('input',()=>{taskPage=0;filterTasks();});
  $('#task-prev').onclick=()=>{if(taskPage>0){taskPage--;filterTasks();}};
  $('#task-next').onclick=()=>{const pages=Math.ceil(matchingTasks().length/(Number($('#task-page-size').value)||25));if(taskPage+1<pages){taskPage++;filterTasks();}};
  const selectVisibleTasks=$('#select-visible-tasks');
  if(selectVisibleTasks)selectVisibleTasks.onchange=event=>{for(const task of tasks().filter(routeEligible)){if(event.target.checked)checkedTasks.add(task.id);else checkedTasks.delete(task.id);}updateBulkSelection();};
  $('#bulk-clear').onclick=()=>{checkedTasks.clear();updateBulkSelection();};
  $('#bulk-remove').onclick=()=>removeQueueEntries([...checkedTasks]);
  function bulkRoutePayload(task, options) {
    return {pool:options.pool==='keep'?task.policy:options.pool,
      preferred_worker_id:options.worker==='keep'?(task.preferred_worker_id||null):(options.worker||null),
      min_vram_gb:options.vram===''?task.min_vram_gb:Number(options.vram),
      architecture:options.architecture==='keep'?task.architecture:options.architecture,
      requeue:['failed','cancelled','completed'].includes(task.status)};
  }
  $('#bulk-apply').onclick=async()=>{
    const selectedTasks=[...checkedTasks].map(id=>state.tasks.find(task=>task.id===id)).filter(routeEligible);
    if(!selectedTasks.length)return;
    if(!$('#bulk-vram').reportValidity())return;
    if(!window.confirm(`Apply changed route settings to ${selectedTasks.length} selected task${selectedTasks.length===1?'':'s'}? Fields marked Keep current are preserved. Completed, failed, and cancelled tasks will be requeued.`))return;
    const options={pool:$('#bulk-pool').value,worker:$('#bulk-worker').value,vram:$('#bulk-vram').value,architecture:$('#bulk-architecture').value};
    $('#bulk-apply').disabled=true;
    $('#bulk-route-status').textContent='Updating selected routes…';
    const results=await Promise.all(selectedTasks.map(async task=>{
      try {
      const response=await fetch(`/api/v1/operator/tasks/${encodeURIComponent(task.id)}/route`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(bulkRoutePayload(task,options))});
      let body={};try{body=await response.json();}catch{}
      return {task,response,body};
      } catch(error){return {task,response:{ok:false},body:{detail:error.message}};}
    }));
    const failures=results.filter(item=>!item.response.ok);
    for(const item of results)if(item.response.ok)checkedTasks.delete(item.task.id);
    $('#bulk-apply').disabled=false;
    await refresh();
    $('#bulk-route-status').textContent=`${results.length-failures.length} route(s) updated.${failures.length?' '+failures.length+' failed: '+(failures[0].body.detail||'Request failed'):' Queue requirements now reflect the saved routes.'}`;
    if(failures.length)window.alert(`${failures.length} task${failures.length===1?'':'s'} could not be changed. ${failures[0].body.detail||'Sign in under Rentals and try again.'}`);
  };
  $('#worker-search').addEventListener('input',filterWorkers);
  for(const id of ['log-search','log-filter','selected-logs']) $('#'+id).addEventListener('input',renderLogs);
  function download(name,content,type) { const url=URL.createObjectURL(new Blob([content],{type})), a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
  $('#export-tasks').onclick=()=>{const cell=v=>`"${String(v??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')}"`;const rows=[['Task ID','Job','Status','Worker','Progress','Runtime','Submitted','Generation seconds'],...matchingTasks().map(t=>[t.id,t.job_name,t.status,t.worker_id,t.progress,t.runtime,t.created_at,t.artifact?.timings?.generation_seconds])];download('tensor-master-tasks.csv',rows.map(row=>row.map(cell).join(',')).join('\r\n'),'text/csv');};
  $('#export-logs').onclick=()=>download('tensor-master-events.log',visibleEvents().map(e=>`${e.timestamp} [${e.kind}] ${e.message}`).join('\n'),'text/plain');
  function openGeneratorJob(jobId) {
    if(!window.confirm('Load this job into Generate? Current form edits will be replaced.'))return;
    location.hash='#generator';nav();window.tensorGenerator.loadJob(jobId);
  }
  function nav() {
    if(location.hash==='#ltx')location.hash='#generator';
    const route=location.hash||'#generator', generating=['#generator','#sources'].includes(route), outputs=route==='#outputs';
    if(!outputs)closeOutputPlayer();
    document.body.classList.toggle('generator-open',generating);document.body.classList.toggle('output-open',outputs);
    $('#generator').hidden=!generating;$('#outputs').hidden=!outputs;
    $('#workspace-title').textContent=route==='#sources'?'Sources':generating?'Generate':outputs?'Outputs':'Dispatcher';

    if(route==='#sources')window.tensorGenerator.showSources();
    else if(route==='#generator')window.tensorGenerator.showGenerate();
    const dispatching=!generating&&!outputs;
    $('#dispatcher-nav').open=false;
    $('#dispatcher-nav').classList.toggle('active',dispatching);
    syncModeNavigation();
    if(outputs)loadDurableOutputs();
    document.querySelectorAll('.workspace-tabs>a:not([data-studio-mode])').forEach(a=>a.classList.toggle('active',a.hash===(location.hash||'#generator')));
    if(location.hash==='#history')$('#history')?.setAttribute('open','');
  }
  function syncModeNavigation() {
    const mode=document.querySelector('#studio-form')?.dataset.mode || 'h3';
    const generating=(!location.hash || location.hash==='#generator');
    document.querySelectorAll('[data-studio-mode]').forEach(tab=>{
      const active=generating && tab.dataset.studioMode===mode;
      tab.classList.toggle('active',active);
      if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');
    });
  }
  document.querySelectorAll('[data-studio-mode]').forEach(tab=>tab.addEventListener('click',event=>{
    event.preventDefault();location.hash='#generator';window.tensorGenerator.setMode(tab.dataset.studioMode);syncModeNavigation();
  }));
  document.addEventListener('generator-mode-change',syncModeNavigation);
  document.querySelectorAll('#dispatcher-nav a').forEach(link=>link.addEventListener('click',()=>{$('#dispatcher-nav').open=false;}));
  document.addEventListener('click',event=>{if(!event.target.closest('#dispatcher-nav'))$('#dispatcher-nav').open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')$('#dispatcher-nav').open=false;});
  function closeOutputPlayer(){
    const dialog=$('#output-player');
    if(dialog.open)dialog.close();
  }
  function openOutputPlayer(file){
    const url=outputUrl(file);if(!url)return;
    const dialog=$('#output-player'),video=$('#output-player-video');
    video.pause();video.src=url;
    $('#output-player-title').textContent=file.job_name||file.filename;
    $('#output-player-status').textContent='';
    const download=$('#output-player-download');download.href=url;download.download=file.filename;
    $('#output-player-original').href=url;
    if(!dialog.open)dialog.showModal();
    video.play().catch(()=>{if(dialog.open&&video.getAttribute('src')===url)$('#output-player-status').textContent='Press Play to start the video.';});
  }
  $('#output-player-close').onclick=closeOutputPlayer;
  $('#output-player').addEventListener('close',()=>{
    const video=$('#output-player-video');video.pause();video.removeAttribute('src');video.load();
  });
  $('#output-player').addEventListener('click',event=>{
    if(event.target!==event.currentTarget)return;
    const box=event.currentTarget.getBoundingClientRect();
    if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeOutputPlayer();
  });
  $('#output-player-video').addEventListener('error',()=>{
    if($('#output-player').open)$('#output-player-status').textContent='This video could not play in your browser. Try Download or Open in new tab.';
  });
  const outputSpecs = new Map();
  function outputSettings(file) {
    const details = el('details', null, 'output-settings');
    const summary = el('summary', 'Settings & job details');
    const body = el('div');
    const provenance=el('pre',JSON.stringify({job_id:file.job_id||null,task_id:file.task_id||null,prompt_id:file.prompt_id||null,recipe:file.recipe||null,timings:file.timings||null},null,2));
    details.append(summary, provenance, body);
    let loading = false, loaded = false;
    details.ontoggle = async () => {
      if (!details.open || loading || loaded) return;
      if (!file.task_id) {
        body.replaceChildren(el('p', file.recipe?'Saved recipe shown above. Additional task settings are unavailable.':'Render settings were not recorded for this output.', 'inspect-note'));
        loaded = true;
        return;
      }
      loading = true;
      body.replaceChildren(specSection(null));
      try {
        if (!outputSpecs.has(file.task_id)) {
          const request = fetch(`/api/v1/tasks/${encodeURIComponent(file.task_id)}/spec`)
            .then(response => {
              if (!response.ok) throw new Error('Settings unavailable');
              return response.json();
            }).catch(error => { outputSpecs.delete(file.task_id); throw error; });
          outputSpecs.set(file.task_id, request);
        }
        body.replaceChildren(specSection(await outputSpecs.get(file.task_id)));
        loaded = true;
      } catch {
        body.replaceChildren(el('p', 'Could not load render settings. Close and reopen to retry.', 'inspect-note'));
      } finally {
        loading = false;
      }
    };
    return details;
  }
  function renderDurableOutputs(){
    if(!durableOutputs)return;const term=$('#output-search').value.trim().toLowerCase(),size=Number($('#output-page-size').value)||24;
    const matches=durableOutputs.filter(file=>[file.job_name,file.job_id,file.task_id,file.prompt_id,file.worker_id,file.recipe?.sourceDirection,file.recipe?.direction,file.recipe?.aiPrompt].join(' ').toLowerCase().includes(term));
    const pages=Math.max(1,Math.ceil(matches.length/size));outputPage=Math.min(outputPage,pages-1);const shown=matches.slice(outputPage*size,(outputPage+1)*size),grid=$('#output-grid');grid.replaceChildren();
    const active=file=>["Queued","Running","Cancelling"].includes(file.status);
    shownOutputs=shown.filter(file=>!active(file));
    const known=new Set(durableOutputs.filter(file=>!active(file)).map(file=>file.prompt_id));for(const id of checkedOutputs)if(!known.has(id))checkedOutputs.delete(id);
    for(const file of shown){
      const card=el('article',null,'output-card gallery-card'),media=file.filename?document.createElement('img'):window.tensorGenerator.outputSlate(file),query=new URLSearchParams({filename:file.filename,subfolder:file.subfolder||'',type:file.type||'output'});
      if(file.task_id&&!String(file.worker_id||'').startsWith('local'))query.set('task_id',file.task_id);
      if(file.filename){media.src=`/api/v1/media/preview?${query}`;media.loading='lazy';media.alt='Completed render';}
      const title=el('h3',file.job_name||file.filename),original=file.recipe?.sourceDirection||file.recipe?.direction||'',prompt=el('p',original||'No prompt recorded'),meta=el('div',`${file.worker_id||'unknown worker'} · ${date(file.modified?file.modified*1000:null)}`,'output-meta'),actions=window.tensorGenerator.outputActions(file,media);actions.classList.add('output-actions');
      const identity=el('div','Job: ','output-job-id');identity.append(el('code',file.job_id||file.prompt_id||'Not recorded'));
      const timing=el('div',file.timings?.generation_seconds==null?'Render time not recorded':`Render: ${seconds(file.timings.generation_seconds)}${file.timings.queue_seconds==null?'':` · Queue: ${seconds(file.timings.queue_seconds)}`}`,'output-timing');
      const enhanced=file.recipe?.aiPrompt;
      const selection=el('label',' Select entry'),check=document.createElement('input');check.type='checkbox';check.checked=checkedOutputs.has(file.prompt_id);check.disabled=removingOutputs||active(file);check.style.width='auto';check.onchange=()=>{if(check.checked)checkedOutputs.add(file.prompt_id);else checkedOutputs.delete(file.prompt_id);updateOutputSelection();};selection.prepend(check);
      const copy=el('div',null,'gallery-card-copy');
      card.append(media,copy);copy.append(selection,title,identity,prompt);
      if(enhanced&&enhanced!==original){const details=el('details',null,'output-ai-prompt'),summary=el('summary','AI enhanced prompt'),text=el('pre',enhanced);details.append(summary,text);copy.append(details);}
      if(file.job_id&&!file.recipe){const edit=el('button','Edit & rerun');edit.type='button';edit.onclick=()=>openGeneratorJob(file.job_id);actions.prepend(edit);}
      const remove=el('button','Remove entry','danger');remove.type='button';remove.disabled=removingOutputs||active(file);remove.title='Remove from Outputs and Gallery; keep media file';remove.onclick=()=>removeOutputEntries([file.prompt_id]);actions.append(remove);
      copy.append(timing,meta,outputSettings(file),actions);grid.append(card);
    }
    $('#output-status').textContent=`${matches.length} jobs · ${durableOutputs.filter(active).length} queued or rendering · ${durableOutputs.length} total`;
    $('#output-page-label').textContent=`Page ${outputPage+1} of ${pages}`;$('#output-prev').disabled=outputPage===0;$('#output-next').disabled=outputPage+1>=pages;
    updateOutputSelection();
  }
  function updateOutputSelection(){const button=$('#output-remove-selected');button.textContent=removingOutputs?'Removing…':`Remove selected (${checkedOutputs.size})`;button.disabled=removingOutputs||!checkedOutputs.size;$('#output-select-page').disabled=removingOutputs||!shownOutputs.length;$('#output-clear-selection').disabled=removingOutputs||!checkedOutputs.size;}
  async function loadDurableOutputs(){
    if(removingOutputs)return;
    const load=++outputLoad;
    $('#output-status').textContent='Refreshing outputs…';
    try{
      const options={cache:'no-store',signal:AbortSignal.timeout(30000)};
      const [response,status]=await Promise.all([fetch('/api/v1/outputs',options),fetch('/api/v1/generator/status',options)]);
      if(!response.ok||!status.ok)throw Error('Output index unavailable');
      const [files,settings]=await Promise.all([response.json(),status.json()]);
      if(load!==outputLoad)return;
      const hidden=new Set(settings.hidden_ids||[]);
      durableOutputs=files.filter(file=>!hidden.has(file.prompt_id));renderDurableOutputs();
    }catch(error){if(load===outputLoad)$('#output-status').textContent=`Could not refresh outputs. ${error.name==='TimeoutError'?'Request timed out.':error.message} Click Refresh to retry.`;}
  }
  $('#output-select-page').onclick=()=>{for(const file of shownOutputs)checkedOutputs.add(file.prompt_id);renderDurableOutputs();};
  $('#output-clear-selection').onclick=()=>{checkedOutputs.clear();renderDurableOutputs();};
  async function removeOutputEntries(ids){
    if(removingOutputs||!ids.length)return;
    if(!window.confirm(`Remove ${ids.length} entries from Outputs and Gallery? Media files stay on disk.`))return;
    for(const id of ids)checkedOutputs.add(id);
    removingOutputs=true;++outputLoad;renderDurableOutputs();let failed=0;
    for(const id of ids){try{const response=await fetch('/api/v1/generator/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt_id:id,saved:false,hidden:true})});if(!response.ok)throw Error();checkedOutputs.delete(id);durableOutputs=durableOutputs.filter(file=>file.prompt_id!==id);}catch{failed++;}}
    removingOutputs=false;renderDurableOutputs();$('#output-status').textContent=failed?`${ids.length-failed} removed; ${failed} failed. Failed entries remain selected for retry.`:`${ids.length} entries removed. Media files kept.`;
  }
  $('#output-remove-selected').onclick=()=>removeOutputEntries([...checkedOutputs]);
  $('#output-search').addEventListener('input',()=>{outputPage=0;renderDurableOutputs();});$('#output-page-size').addEventListener('change',()=>{outputPage=0;renderDurableOutputs();});$('#output-prev').onclick=()=>{outputPage--;renderDurableOutputs();};$('#output-next').onclick=()=>{outputPage++;renderDurableOutputs();};
  function syncNavCounts(metrics) {
    for (const [selector, key] of [['[data-dispatch-queued]', 'queued'], ['[data-dispatch-running]', 'running'], ['[data-dispatch-workers]', 'active_workers']]) {
      document.querySelectorAll(selector).forEach(badge => { badge.textContent = String(metrics[key] ?? 0); });
    }
    document.querySelectorAll('[data-live-queued]').forEach(badge=>{badge.textContent=String(metrics.queued ?? 0);});
    for(const [hash,key] of [['monitor','running'],['tasks','active_tasks'],['workers','active_workers']]) {
      document.querySelectorAll(`a[href="#${hash}"] span`).forEach(badge=>{badge.textContent=String(metrics[key] ?? 0);});
    }
  }
  function onJobSubmitted() {
    if(refreshing){refreshPending=true;return;}
    void refresh();
    if(location.hash==='#outputs')void loadDurableOutputs();
  }
  window.addEventListener('tensor-master:job-submitted',onJobSubmitted);
  async function refresh() {
    if(refreshing)return;refreshing=true;
    try {
      const response=await fetch(location.pathname,{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error('Dashboard unavailable');
      const doc=parseLiveDashboard(await response.text());
      const next=JSON.parse(doc.querySelector('#console-state').textContent);
      const focused=document.activeElement?.closest('#tasks [data-task-id]');
      const focusedId=focused?.dataset.taskId, focusedTag=focused?.tagName;
      const restore=preservePosition();
      // Never replace the queue under a priority number that is being typed.
      const editingPriority=document.activeElement?.classList.contains('priority-input');
      // Replace only live table regions. Search, scroll, inspector, media and dialogs stay put.
      for(const selector of ['.metrics','#tasks .table-wrap','#tasks .mobile-task-list','#tasks .empty','#task-count','.worker-table','.mobile-worker-list']) {
        if(editingPriority&&selector.startsWith('#tasks'))continue;
        const target=$(selector), fresh=doc.querySelector(selector);
        if(target&&fresh){const y=target.scrollTop,x=target.scrollLeft;syncLiveRegion(target,fresh);target.scrollTop=y;target.scrollLeft=x;}
        else if(!target&&fresh&&selector.startsWith('#tasks'))$('#tasks .table-footer').before(fresh);
        else if(target&&!fresh)target.remove();
      }
      const rental=doc.querySelector('[aria-label="Cloud rental fleet"]'), current=$('[aria-label="Cloud rental fleet"]');
      if(rental&&current)syncLiveRegion(current,rental);
      else if(rental&&!current)$('main').append(rental);
      else if(current&&!rental)current.remove();
      state=next;syncNavCounts(state.metrics);if(!selected)selected=state.tasks[0]?.id;syncBulkWorkers();
      filterTasks();filterWorkers();renderLogs();renderInspector();
      if(focusedId){const replacement=[...document.querySelectorAll('#tasks [data-task-id]')].find(row=>row.dataset.taskId===focusedId&&row.tagName===focusedTag&&!row.hidden);if(replacement)focusTask(replacement);}
      restore();
      $('#sync-status').textContent=`Live · ${new Date().toLocaleTimeString()}`;$('#sync-status').classList.remove('stale');
    } catch {$('#sync-status').textContent='Disconnected · data stale';$('#sync-status').classList.add('stale');}
    finally{refreshing=false;if(refreshPending){refreshPending=false;void refresh();}}
  }
  $('#pause-live').onclick=()=>{paused=!paused;$('#pause-live').textContent=paused?'Resume updates':'Pause updates';if(paused){$('#sync-status').textContent='Updates paused';$('#sync-status').classList.add('stale');}else refresh();};
  document.addEventListener('click',async event=>{
    const button=event.target.closest?.('[data-restart-worker]');
    if(!button||button.disabled)return;
    button.disabled=true;button.textContent='Restarting…';
    try{
      const response=await fetch(`/api/v1/operator/workers/${encodeURIComponent(button.dataset.restartWorker)}/restart`,{method:'POST'});
      const body=await response.json();
      if(!response.ok)throw Error(body.detail||'Worker restart failed');
      await refresh();
    }catch(error){window.alert(error.message);button.disabled=false;button.textContent='Restart worker';}
  });
  async function refreshVisiblePage(){
    const button=$('#refresh-console');
    if(button.disabled)return;
    button.disabled=true;button.textContent='Refreshing…';
    try{await Promise.all([refresh(),location.hash==='#outputs'?loadDurableOutputs():Promise.resolve()]);}
    finally{button.disabled=false;button.textContent='Refresh';}
  }
  $('#refresh-console').onclick=refreshVisiblePage;
  syncBulkWorkers();filterTasks();filterWorkers();renderLogs();renderInspector();$('#sync-status').textContent='Live · refresh every 5s';
  setInterval(()=>{if(!paused&&!document.hidden){void refresh();if(location.hash==='#outputs')void loadDurableOutputs();}},5000);
})();
