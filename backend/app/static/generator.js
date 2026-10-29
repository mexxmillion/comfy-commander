/* Tensor Master native Generator; derived from Pocket AI (MIT). */
(() => {
const studioRoot = document.querySelector("#generator");
const $ = (selector) => studioRoot.querySelector(selector);
const state = {
  mode: "h3",
  h3Type: "reference",
  files: [],
  savedReferences: [],
  frameFiles: { first_frame: null, last_frame: null },
  savedFrameRefs: {},
  loras: [],
  selectedLoras: new Map(),
  dispatches: new Map(),
  preparing: 0,
  runCounter: 0,
  submitting: false,
  queueTimer: null,
  promptDirectorProfile: "Qwen 3.5 9B Heretic — 160K Vision",
  promptDirectorReady: false,
  qwenReady: false,
  lastOutput: null,
  galleryFiles: [],
  galleryPage: 0,
  controllerMedia: true,
  aiPrompts: { h3: "", qwen: "" },
  aiPromptSources: { h3: "", qwen: "" },
  runPrompt: null,
};
const DRAFT_KEY = 'tensor-master.generator-draft.v1';
let draftReady = false, draftEarlyEdit = false, draftQueuedFiles = null, draftDatabase, draftLastSnapshot;

function draftControls() {
  return [...$('#studio-form').querySelectorAll('input,select,textarea')].filter(control =>
    !['file','password','button','submit'].includes(control.type) && (control.id || control.type === 'radio'));
}

function draftFilesSignature(files, frames) {
  const describe = file => file ? [file.name,file.size,file.type,file.lastModified] : null;
  return JSON.stringify([files.map(describe),describe(frames.first_frame),describe(frames.last_frame)]);
}

function openDraftDatabase() {
  if (!draftDatabase) draftDatabase = new Promise((resolve,reject) => {
    const request = indexedDB.open('tensor-master-generator-drafts',1);
    request.onupgradeneeded = () => request.result.createObjectStore('references');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('Browser draft storage is blocked'));
  });
  return draftDatabase;
}

async function storeDraftFiles(files, frames, signature) {
  const database = await openDraftDatabase();
  await new Promise((resolve,reject) => {
    const transaction = database.transaction('references','readwrite');
    transaction.objectStore('references').put({files,frames,signature},'latest');
    transaction.oncomplete = resolve;
    transaction.onerror = transaction.onabort = () => reject(transaction.error);
  });
}

function captureDraft() {
  return {version:1,mode:state.mode,h3Type:state.h3Type,
      controls:draftControls().map(control=>({id:control.id,name:control.name,type:control.type,value:control.value,checked:control.checked,
        nativeSteps:control.dataset.nativeSteps,turboActive:control.dataset.turboActive})),
      savedReferences:state.savedReferences,savedFrameRefs:state.savedFrameRefs,loras:[...state.selectedLoras],
      aiPrompts:state.aiPrompts,aiPromptSources:state.aiPromptSources,kreaSeed:state.kreaSeed,restoredCreative:state.restoredCreative,
      filesSignature:draftFilesSignature(state.files,state.frameFiles),
      pendingNames:[...state.files,...Object.values(state.frameFiles).filter(Boolean)].map(file=>file.name)};
}

function saveDraft() {
  if (!draftReady) return;
  const notice = $('#draft-status');
  const files = [...state.files], frames = {...state.frameFiles};
  const draft=captureDraft(),signature=draft.filesSignature,snapshot=JSON.stringify(draft);
  try {
    // An untouched older tab must not overwrite a newer draft when it closes.
    if(snapshot!==draftLastSnapshot) {
      localStorage.setItem(DRAFT_KEY,JSON.stringify({...draft,updated:Date.now()}));
      draftLastSnapshot=snapshot;
    }
  } catch(error) {
    notice.textContent='Draft could not be saved in this browser. Keep this tab open or save a reusable setup.';
    return;
  }
  if (signature === draftQueuedFiles) return;
  draftQueuedFiles = signature;
  notice.textContent='Saving draft and reference files on this device…';
  storeDraftFiles(files,frames,signature).then(()=>{
    if (signature===draftQueuedFiles) notice.textContent='Draft saved on this device · prompt, settings and references';
  }).catch(()=>{
    if (signature===draftQueuedFiles) {
      draftQueuedFiles=null;
      notice.textContent='Prompt and settings saved. Browser storage could not save local files; reselect them after reopening.';
    }
  });
}

function scheduleDraftSave() {
  if (draftReady) queueMicrotask(saveDraft);
}

async function restoreDraft() {
  const notice=$('#draft-status');
  try {
    const draft=JSON.parse(localStorage.getItem(DRAFT_KEY)||'null');
    if (!draft || draft.version!==1 || !['h3','qwen','faces','krea','ltx','voice'].includes(draft.mode)) return;
    let stored=null;
    if (draft.pendingNames?.length) {
      try {
        const database=await openDraftDatabase();
        stored=await new Promise((resolve,reject)=>{
          const request=database.transaction('references','readonly').objectStore('references').get('latest');
          request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
        });
      } catch {}
    }
    // Never replace edits made while the controller was connecting.
    if (draftEarlyEdit) return;
    setMode(draft.mode);setH3Type(draft.h3Type);
    const controls=draftControls();
    for (const saved of draft.controls||[]) {
      const control=controls.find(item=>saved.id?item.id===saved.id:item.type==='radio'&&item.name===saved.name&&item.value===saved.value);
      if (!control || control.type!==saved.type) continue;
      if (control.tagName!=='SELECT'||[...control.options].some(option=>option.value===saved.value)) control.value=saved.value;
      if (['radio','checkbox'].includes(control.type)) control.checked=Boolean(saved.checked);
      if (saved.nativeSteps!==undefined) control.dataset.nativeSteps=saved.nativeSteps;
      if (saved.turboActive!==undefined) control.dataset.turboActive=saved.turboActive;
    }
    if (!studioRoot.querySelector('input[name="turbo"]:checked')) {
      studioRoot.querySelector('input[name="turbo"][value="4"]').checked = true;
      $("#turbo-enabled").checked = false;
      $("#total-steps").value = 4;
    }
    if (!(draft.controls || []).some(control => control.id === "sparse-enabled")) sparseAttentionDefaults();
    state.savedReferences=draft.savedReferences||[];state.savedFrameRefs=draft.savedFrameRefs||{};
    state.selectedLoras=new Map(draft.loras||[]);
    state.aiPrompts=draft.aiPrompts||{h3:'',qwen:''};state.aiPromptSources=draft.aiPromptSources||{h3:'',qwen:''};
    state.kreaSeed=draft.kreaSeed;state.restoredCreative=draft.restoredCreative;
    const restoredFiles=stored?.signature===draft.filesSignature;
    state.files=restoredFiles?stored.files:[];
    state.frameFiles=restoredFiles?stored.frames:{first_frame:null,last_frame:null};
    if (restoredFiles) draftQueuedFiles=stored.signature;
    if (state.mode==='h3') syncPromptEnhancement();
    if (state.mode==='qwen') {syncQwenTurbo();syncQwenEnhancement();}
    syncVoiceMode();syncPromptOnly();syncQwenEditCanvas();
    renderReferences();renderFrameInputs();renderSelectedLoras();renderLoraList();
    window.creativeMaster?.sync();
    notice.textContent=draft.pendingNames?.length&&!restoredFiles
      ? `Draft restored. Reselect files that could not be saved: ${draft.pendingNames.join(', ')}`
      : 'Last draft restored · nothing has been submitted';
    if (draft.pendingNames?.length&&!restoredFiles) return;
  } catch {
    notice.textContent='The saved draft could not be restored. Your current form has been kept.';
  } finally {
    draftLastSnapshot=JSON.stringify(captureDraft());
    draftReady=true;
  }
}

const TURBO_LORAS = {
  4: "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
  8: "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
};
const FRAME_TURBO_LORAS = {
  4: "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
  8: "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
};
const FRAME_H3_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors";
const LEGACY_H3_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors";
const PRESET_STEPS = { 4: 4, 8: 8 };
const VSA_LORA = "fasth3_vsa_gate.safetensors";
const EROSMAX_MODEL = "__private_model_not_available_in_community__";
const SINGULARITY_MODEL = 'Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors';
const OMNI_MODEL = 'fasth3_omniref_8step_pruned_int8_convrot.safetensors';
const H3_TEXT_ENCODER = 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors';
const QWEN21_MODEL = "qwen_image_2.1_int8_convrot.safetensors";
const QWEN21_CLIP = "qwen3vl_8b_int8_convrot.safetensors";
const QWEN21_VAE = "qwen_image_2.1_vae_bf16.safetensors";
const QWEN21_TURBO = "Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors";
// H3 Face Refine keeps the verified recipe fixed; ErosMax has turbo built in, the others need the 8-step LoRA.
const FACE_REFINE_TURBO = FRAME_TURBO_LORAS[8];
const FACE_REFINE_PROMPT = "<Picture 1> is the same person in the source video. Preserve identity, pose, lighting, expression and scene while refining the face.";
const SAVED_SETUPS_KEY = "pocket-studio-saved-h3-setups";

const toast = (message) => {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3500);
};

function addLog(message, level = "info") {
  const log = $("#run-log");
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const prefix = level === "error" ? "ERROR" : level === "success" ? "DONE " : "INFO ";
  const lines = `${time}  ${prefix}  ${message}\n${log.textContent}`.split("\n").slice(0, 240);
  log.textContent = lines.join("\n");
  $("#log-state").textContent = level === "error" ? "Error captured" : "Listening";
}

function setConnection(online) {
  const el = $("#connection");
  el.className = `connection ${online ? "online" : "offline"}`;
  el.querySelector("span").textContent = online ? "Tensor Master online" : "Controller offline";
}

function setStatus(title, detail, progress = null) {
  $("#status-copy strong").textContent = title;
  $("#status-copy span").textContent = detail;
  const wrap = $("#progress-wrap");
  wrap.hidden = progress === null;
  if (progress !== null) $("#progress-bar").style.width = `${Math.max(0, Math.min(100, progress))}%`;
}

function showError(message) {
  const error = $("#run-error");
  error.textContent = message;
  error.hidden = false;
  addLog(message, "error");
}

function clearError() {
  $("#run-error").hidden = true;
  $("#run-error").textContent = "";
}

async function updateQueue() {
  try {
    const response = await fetch('/api/v1/generator/status');
    if (!response.ok) throw new Error('Controller unavailable');
    const status = await response.json();
    const copy = `Queue · ${status.running} running · ${status.queued} waiting · ${status.workers} workers online${state.preparing ? ` · ${state.preparing} enhancing` : ''}`;
    if ($('#queue-copy').textContent !== copy) addLog(copy);
    $('#queue-copy').textContent = copy;
    setConnection(true);
    $('#connection span').textContent = status.workers ? `Tensor Master · ${status.workers} workers` : 'Tensor Master · workers offline';
  } catch { setConnection(false); $('#queue-copy').textContent = 'Controller unavailable'; }
}

function confirmSubmission(jobId) {
  setStatus("Submitted", "Ready for another job · follow progress or cancel in Tasks.", null);
  toast("Job submitted to Tensor Master. Track it in Tasks.");
  window.dispatchEvent(new CustomEvent('tensor-master:job-submitted', {detail: {jobId}}));
  void updateQueue();
}

function setSubmitting(submitting) {
  state.submitting = submitting;
  $("#run-button").disabled = submitting;
  studioRoot.querySelectorAll(".mode-switch button").forEach((button) => { button.disabled = submitting; });
}

function isImageOutput(file) {
  return /\.(png|jpe?g|webp)$/i.test(file?.filename || "") || file?.kind === "images";
}

function showResultMedia(file, url, mode) {
  const image = ["qwen", "krea"].includes(mode) || isImageOutput(file);
  $("#result-video").hidden = image || !["h3", "faces", "ltx"].includes(mode);
  $("#result-image").hidden = !image;
  $("#result-audio").hidden = mode !== "voice";
  if (image) $("#result-image").src = url;
  else if (["h3", "faces", "ltx"].includes(mode)) $("#result-video").src = url;
  else $("#result-audio").src = url;
}

async function pollDispatchedJob(jobId) {
  const dispatch = state.dispatches.get(jobId);
  if (!dispatch || dispatch.busy) return;
  dispatch.busy = true;
  const finish = () => { clearInterval(dispatch.timer); state.dispatches.delete(jobId); };
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || 'Job status unavailable');
    const task = job.tasks?.[0];
    if (!task) return;
    if (['queued', 'running'].includes(task.status)) return;
    else if (task.status === 'failed') throw new Error(task.message || 'Worker failed');
    else if (['cancelled', 'cancel_requested'].includes(task.status)) { finish(); addLog(`${jobId}: cancellation requested`); }
    else if (task.status === 'completed') {
      finish();
      if (!task.artifact?.filename) throw new Error('Worker completed without an output');
      const file = {...task.artifact};
      const url = mediaUrl(file);
      state.lastOutput = file;
      showResultMedia(file, url, dispatch.recipe?.mode || 'h3');
      $('#download-link').href = url; $('#download-link').download = file.filename;
      $('#result-panel').hidden = false;
      if (dispatch.recipe) await saveRecipe(task.prompt_id || task.id, {recipe:dispatch.recipe, output:file, timings:{...file.timings,total_seconds:(Date.now()-dispatch.startedAt)/1000}});
      addLog(`Output ready: ${file.filename} (${jobId})`, 'success');
    }
  } catch (error) { finish(); showError(`${jobId}: ${error.message}`); }
  finally { dispatch.busy = false; }
}

function isAudioFile(file) {
  return file.type.startsWith("audio/") || /\.(wav|mp3|m4a|aac|flac|ogg)$/i.test(file.name);
}

function renderFrameInputs() {
  scheduleDraftSave();
  for (const kind of ["first_frame", "last_frame"]) {
    const prefix = kind === "first_frame" ? "first" : "last";
    const file = state.frameFiles[kind];
    const saved = state.savedFrameRefs[kind];
    const preview = $(`#${prefix}-frame-preview`);
    if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
    delete preview.dataset.objectUrl;
    if (file) {
      preview.src = URL.createObjectURL(file);
      preview.dataset.objectUrl = preview.src;
    } else if (saved?.file) {
      const parts = saved.file.split("/");
      preview.src = mediaUrl({ filename: parts.pop(), subfolder: parts.join("/"), type: "input" });
    } else {
      preview.removeAttribute("src");
    }
    preview.hidden = !file && !saved;
    $(`#${prefix}-frame-label`).textContent = file?.name || saved?.file.split("/").pop() || "Choose image";
    $(`#clear-${prefix}-frame`).hidden = !file && !saved;
  }
}

// Keep the filename stable across retries so a lost response cannot duplicate the upload.
async function uploadReferenceFile(file, prefix) {
  // JFIF is JPEG; use the standard suffix accepted by controller and worker loaders.
  const filename = file.name.replace(/\.jfif$/i, '.jpg').replace(/[^a-zA-Z0-9._ -]/g, "_");
  const name = `${prefix}_${Date.now()}_${crypto.randomUUID()}_${filename}`;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const body = new FormData();
    body.append("image", file, name);
    body.append("type", "input");
    body.append("subfolder", "h3_mobile");
    let response;
    try {
      response = await fetch("/api/v1/generator/upload", { method: "POST", body });
      if (response.ok) return await response.json();
    } catch (error) {
      if (attempt < 3 && error instanceof TypeError) {
        setStatus("Retrying reference upload", `${file.name} · attempt ${attempt + 1} of 3`, null);
        await new Promise(resolve => setTimeout(resolve, attempt * 1000));
        continue;
      }
      throw new Error(`Could not upload ${file.name}: ${error.message}. Uploaded references are kept; tap Generate again to retry.`);
    }
    let detail = '';
    try { const result = await response.json(); if (typeof result.detail === 'string') detail = result.detail; } catch {}
    throw new Error(response.status === 413
      ? `${file.name} exceeds the server's upload limit. Choose a smaller file.`
      : `Upload failed for ${file.name} (HTTP ${response.status})${detail ? `: ${detail}` : '.'} Uploaded references are kept.${response.status >= 500 || response.status === 429 ? ' Tap Generate again to retry.' : ''}`);
  }
}

async function uploadFrameReferences() {
  const references = [];
  for (const kind of ["first_frame", "last_frame"]) {
    const file = state.frameFiles[kind];
    if (!file) {
      if (state.savedFrameRefs[kind]) references.push({ ...state.savedFrameRefs[kind] });
      continue;
    }
    setStatus("Uploading keyframes", kind === "first_frame" ? "First frame" : "Last frame", 10);
    const saved = await uploadReferenceFile(file, `h3_${kind}`);
    const reference = { kind, file: [saved.subfolder, saved.name].filter(Boolean).join("/") };
    references.push(reference);
    state.savedFrameRefs[kind] = { ...reference };
    state.frameFiles[kind] = null;
    renderFrameInputs();
  }
  return references;
}

function renderReferences() {
  scheduleDraftSave();
  const list = $("#reference-list");
  list.querySelectorAll("[data-object-url]").forEach(media => URL.revokeObjectURL(media.dataset.objectUrl));
  list.replaceChildren();
  const references = [
    ...state.savedReferences.map((reference) => ({
      name: reference.file.split("/").pop(),
      kind: reference.kind,
      src: (state.controllerMedia && reference.kind === "image" ? previewUrl : mediaUrl)({
        filename: reference.file.split("/").pop(),
        subfolder: reference.file.split("/").slice(0, -1).join("/"),
        type: "input",
      }),
      saved: true,
      reference,
    })),
    ...state.files.map((file) => ({
      name: file.name,
      kind: state.mode === "ltx" ? "video" : isAudioFile(file) ? "audio" : file.type.startsWith("video/") ? "video" : "image",
      src: URL.createObjectURL(file),
      saved: false,
    })),
  ];
  const kindCounts = { image: 0, video: 0, audio: 0 };
  references.forEach((file, index) => {
    const tile = document.createElement("div");
    tile.className = "reference";
    const mediaType = file.kind === "audio" ? "audio" : file.kind === "video" ? "video" : "img";
    const media = document.createElement(mediaType);
    media.src = file.src;
    if (!file.saved) media.dataset.objectUrl = file.src;
    if (mediaType !== "img") media.preload = "none";
    if (media.tagName === "VIDEO") media.muted = state.mode !== "voice";
    if (media.tagName === "AUDIO" || media.tagName === "VIDEO") media.controls = true;
    const kind = document.createElement("span");
    kind.className = "file-kind";
    const label = state.mode === "voice" ? "Voice" : state.mode === "faces" && file.kind === "image" ? "Face" : file.kind === "audio" ? "Audio" : file.kind === "video" ? "Video" : "Picture";
    kindCounts[file.kind] += 1;
    kind.textContent = `${label} ${state.mode === "voice" ? index + 1 : kindCounts[file.kind]}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      if (file.saved) state.savedReferences.splice(index, 1);
      else state.files.splice(index - state.savedReferences.length, 1);
      markAiPromptStale();
      renderReferences();
    });
    const replace = document.createElement("button");
    replace.type = "button"; replace.className = "replace-reference"; replace.textContent = "Replace";
    replace.setAttribute("aria-label", `Replace ${file.name}`);
    replace.onclick = () => {
      const input = document.createElement("input"); input.type = "file"; input.accept = `${file.kind}/*`;
      const target = file.saved ? state.savedReferences[index] : state.files[index - state.savedReferences.length];
      input.onchange = async () => {
        const replacement = input.files[0]; if (!replacement) return;
        try {
          if (file.saved) {
            replace.disabled = true;
            const uploaded = await uploadReferenceFile(replacement, "reference");
            const position = state.savedReferences.indexOf(target);
            if (position >= 0) state.savedReferences[position] = {kind: file.kind, file: [uploaded.subfolder, uploaded.name].filter(Boolean).join("/")};
          } else {
            const position = state.files.indexOf(target);
            if (position >= 0) state.files[position] = replacement;
          }
          markAiPromptStale(); renderReferences();
        } catch (error) { replace.disabled = false; toast(error.message); }
      };
      input.click();
    };
    tile.append(media, kind, remove, replace);
    if (file.kind === "image" && ["h3", "qwen", "krea"].includes(state.mode)) {
      const isolate = document.createElement("button");
      isolate.type = "button"; isolate.className = "isolate-reference";
      isolate.textContent = "Process";
      isolate.onclick = () => openIsolationEditor(index);
      tile.append(isolate);
      if (file.reference?.isolation || file.reference?.depth || file.reference?.preprocess) {
        const settings = file.reference.isolation || file.reference.preprocess;
        kind.textContent += `${settings ? ` · isolate: ${settings.text}` : ""}${file.reference.depth ? " · depth" : ""}`;
        const reset = document.createElement("button");
        reset.type = "button"; reset.className = "reset-isolation"; reset.textContent = "Original";
        reset.onclick = () => {
          const position = state.savedReferences.indexOf(file.reference);
          if (position < 0) return;
          state.savedReferences[position] = {kind: "image", file: file.reference.preprocess?.source || file.reference.file};
          markAiPromptStale(); renderReferences();
        };
        tile.append(reset);
      }
    }
    list.append(tile);
  });
  syncQwenEditCanvas();
}

let isolationEditor = null;
function referenceFileUrl(file) {
  const parts = file.split("/");
  return mediaUrl({filename: parts.pop(), subfolder: parts.join("/"), type: "input"});
}

async function openIsolationEditor(index) {
  if (state.submitting) return toast("Wait for the current submission to finish.");
  const mode = state.mode;
  const original = index < state.savedReferences.length ? state.savedReferences[index] : state.files[index - state.savedReferences.length];
  try {
    // Upload all pending references in their existing order; never reorder image tags.
    setSubmitting(true);
    await uploadReferences();
    if (mode !== state.mode) return;
    const target = state.savedReferences[index];
    if (!target || (original?.file && target !== original)) throw new Error("Reference changed. Reopen its editor.");
    isolationEditor = {target, mode, result: null, busy: false};
    const previous = target.isolation || target.preprocess;
    $("#isolation-text").value = previous?.text || "person";
    $("#isolation-confidence").value = previous?.confidence ?? 0.25;
    $("#isolation-selection").value = previous?.selection || "best";
    $("#isolation-crop").checked = previous?.crop ?? true;
    $("#isolation-background").value = previous?.background || "gray";
    $("#isolation-enabled").checked = Boolean(target.isolation || (target.preprocess && target.preprocess.isolate !== false));
    $("#reference-depth").checked = Boolean(target.depth || target.preprocess?.depth);
    $("#reference-depth-invert").checked = target.depth?.invert ?? target.preprocess?.invert_depth ?? false;
    $("#reference-cache").checked = target.preprocess_cache === true;
    $("#isolation-view").value = "composite";
    $("#isolation-original").src = referenceFileUrl(previous?.source || target.file);
    $("#isolation-preview").hidden = true;
    $(".isolation-previews").hidden = true;
    $("#reference-process-preview").open = false;
    $("#isolation-apply").disabled = false;
    $("#isolation-status").textContent = "Save settings, then Generate. Unchanged isolation/depth results are reused without loading their models.";
    setIsolationBusy(false);
    $("#reference-isolation").showModal();
    setStatus("Reference ready", "Choose processing settings", null);
  } catch (error) { toast(error.message); }
  finally { setSubmitting(false); }
}

function setIsolationBusy(busy) {
  if (isolationEditor) isolationEditor.busy = busy;
  for (const id of ["isolation-text", "isolation-confidence", "isolation-selection", "isolation-crop", "isolation-background", "isolation-find", "isolation-enabled", "reference-depth", "reference-depth-invert", "reference-cache"]) $("#" + id).disabled = busy;
  for (const id of ["isolation-text", "isolation-confidence", "isolation-selection", "isolation-crop", "isolation-background"]) $("#" + id).disabled = busy || !$("#isolation-enabled").checked;
  $("#reference-depth-invert").disabled = busy || !$("#reference-depth").checked;
}

function showIsolationPreview() {
  const result = isolationEditor?.result;
  if (!result) return;
  const view = $("#isolation-view").value;
  $("#isolation-preview").src = referenceFileUrl(view === "composite" ? result.file : result.preprocess[view]);
  $("#isolation-preview").hidden = false;
  $(".isolation-previews").hidden = false;
  $("#isolation-preview-label").textContent = view === "mask" ? "Mask · white is kept" : view === "composite" ? "Generation reference" : "Cutout preview";
}

async function previewIsolation() {
  const editor = isolationEditor;
  if (!editor || editor.busy) return;
  const text = $("#isolation-text").value.trim();
  const confidence = Number($("#isolation-confidence").value);
  if ($("#isolation-enabled").checked && !text) return toast("Describe what to isolate, for example shirt.");
  if (!$("#isolation-enabled").checked && !$("#reference-depth").checked) return toast("Enable isolation or depth to preview preprocessing.");
  if (!Number.isFinite(confidence) || confidence < 0.05 || confidence > 0.95) return toast("Confidence must be between 0.05 and 0.95.");
  const target = $("#render-target").value;
  const policy = target === "tensor-master-cloud" ? "standard" : target === "tensor-master-any" ? "any" : "local";
  editor.result = null;
  $("#isolation-apply").disabled = true;
  $("#isolation-preview").hidden = true;
  $("#isolation-status").textContent = "Submitting reference processing…";
  setIsolationBusy(true);
  try {
    const response = await fetch("/api/v1/generator/isolate", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({file: editor.target.preprocess?.source || editor.target.file, text: text || "person", confidence,
        isolate: $("#isolation-enabled").checked, depth: $("#reference-depth").checked, invert_depth: $("#reference-depth-invert").checked,
        use_cache: $("#reference-cache").checked, selection: $("#isolation-selection").value,
        crop: $("#isolation-crop").checked, background: $("#isolation-background").value, policy})});
    const submitted = await response.json();
    if (!response.ok) throw new Error(typeof submitted.detail === "string" ? submitted.detail : "Isolation request rejected");
    let result = submitted;
    while (!result.reference) {
      if (["failed", "cancelled"].includes(result.status)) throw new Error(result.message || "Isolation did not complete. See Tasks for details.");
      if (editor === isolationEditor) $("#isolation-status").textContent = result.status === "queued" ? "Queued · waiting for a worker with reference processing installed. Follow or cancel this job in Tasks." : (result.message || "Processing reference…");
      await new Promise(resolve => setTimeout(resolve, 2500));
      const status = await fetch(`/api/v1/generator/isolate/${encodeURIComponent(submitted.job_id)}`);
      result = await status.json();
      if (!status.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Could not read isolation status. See Tasks.");
    }
    if (editor !== isolationEditor) return;
    editor.result = result.reference;
    showIsolationPreview();
    $("#isolation-apply").disabled = false;
    $("#isolation-status").textContent = "Processed result ready. Cache is saved when reuse is enabled. Save settings; Generate uses the same processing automatically.";
  } catch (error) {
    if (editor === isolationEditor) $("#isolation-status").textContent = error.message;
  } finally { if (editor === isolationEditor) { setIsolationBusy(false); $("#isolation-apply").disabled = false; } }
}

function initializeIsolationEvents() {
$("#isolation-find").onclick = previewIsolation;
$("#isolation-view").onchange = showIsolationPreview;
$("#isolation-background").onchange = () => {
  $("#isolation-view").value = "composite";
  if (isolationEditor) isolationEditor.result = null;
  $("#isolation-preview").hidden = true;
};
for (const id of ["isolation-text", "isolation-confidence", "isolation-selection", "isolation-crop", "isolation-enabled", "reference-depth", "reference-depth-invert", "reference-cache"]) {
  $("#" + id).addEventListener("input", () => {
    if (isolationEditor) isolationEditor.result = null;
    $("#isolation-apply").disabled = false;
    $("#isolation-preview").hidden = true;
    setIsolationBusy(false);
  });
}
$("#isolation-apply").onclick = () => {
  const editor = isolationEditor;
  if (!editor || editor.busy) return;
  const position = state.savedReferences.indexOf(editor.target);
  if (editor.mode !== state.mode || position < 0) return toast("Reference changed. Reopen its editor.");
  const text = $("#isolation-text").value.trim();
  const confidence = Number($("#isolation-confidence").value);
  if ($("#isolation-enabled").checked && !text) return toast("Describe what to isolate, for example shirt.");
  if (!Number.isFinite(confidence) || confidence < 0.05 || confidence > 0.95) return toast("Confidence must be between 0.05 and 0.95.");
  state.savedReferences[position] = {kind: "image", file: editor.target.preprocess?.source || editor.target.file,
    preprocess_cache: $("#reference-cache").checked,
    ...($("#isolation-enabled").checked ? {isolation: {text, confidence, selection: $("#isolation-selection").value,
      crop: $("#isolation-crop").checked, background: $("#isolation-background").value}} : {}),
    ...($("#reference-depth").checked ? {depth: {invert: $("#reference-depth-invert").checked}} : {})};
  markAiPromptStale(); renderReferences();
  $("#reference-isolation").close();
  setStatus("Ready to create", "Reference processing settings saved", null);
  toast("Processing settings saved. Generate will reuse cached results when available.");
};
}

function preprocessReferenceImage(workflow, input, reference, number) {
  if (!reference.isolation && !reference.depth) return input;
  const settings = reference.isolation || {};
  const node = String(7000 + number);
  workflow[node] = {class_type: "TMPrepareReference", inputs: {source_file: reference.file,
    isolate: Boolean(reference.isolation), text: settings.text || "person",
    confidence: settings.confidence ?? 0.25, selection: settings.selection || "best",
    depth: Boolean(reference.depth), invert_depth: reference.depth?.invert === true,
    background: settings.background || "gray", crop: settings.crop !== false,
    use_cache: reference.preprocess_cache === true,
    cache_key: "", isolation_key: "", depth_key: "", cached_isolation: "", cached_depth: ""}};
  return [node, 0];
}

function syncQwenEditCanvas() {
  const field = $("#qwen-edit-canvas-field");
  if (!field) return;
  const editing = state.mode === "qwen" && state.files.length + state.savedReferences.length > 0;
  field.hidden = !editing;
  $("#qwen-edit-canvas").disabled = !editing;
}

function addFiles(files, savedReference = null) {
  if (state.mode === "ltx") {
    const incoming = [...files].filter(file => file.type.startsWith("video/") || /\.(mp4|mov|mkv|webm|m4v)$/i.test(file.name));
    if ((savedReference ? 1 : incoming.length) + state.files.length + state.savedReferences.length !== 1 || (savedReference && savedReference.kind !== "video")) {
      toast("Choose one source video. Remove the current source before replacing it.");
      return;
    }
    if (savedReference) state.savedReferences.push(savedReference);
    else state.files.push(...incoming);
    renderReferences(); $("#reference-files").value = "";
    return true;
  }
  const voiceFile = (file) => file.type.startsWith("audio/") || file.type.startsWith("video/") || /\.(wav|mp3|m4a|aac|flac|ogg|mp4|mov|webm)$/i.test(file.name);
  const incoming = [...files].filter((file) => state.mode === "voice" ? voiceFile(file) : file.type.startsWith("image/") || (state.mode === "h3" && (file.type.startsWith("video/") || isAudioFile(file))) || (state.mode === "faces" && file.type.startsWith("video/")));
  const combined = [...state.files, ...incoming];
  const images = [...state.savedReferences.filter(ref => ref.kind === "image"), ...combined.filter((file) => file.type.startsWith("image/"))];
  const videos = [...state.savedReferences.filter(ref => ref.kind === "video"), ...combined.filter((file) => file.type.startsWith("video/"))];
  const audios = [...state.savedReferences.filter(ref => ref.kind === "audio"), ...combined.filter(isAudioFile)];
  if ((state.mode === "h3" && (images.length > 9 || videos.length > 3 || audios.length > 3)) || (state.mode === "qwen" && images.length > 10) || (state.mode === "krea" && images.length > 2) || (state.mode === "voice" && combined.length + state.savedReferences.length > 1) || (state.mode === "faces" && (images.length > 1 || videos.length > 1))) {
    toast(state.mode === "faces" ? "Face Refine takes one video and one face photo." : state.mode === "h3" ? "MiniMax H3 accepts up to 9 photos, 3 videos and 3 audio files." : state.mode === "qwen" ? "Qwen Image 2.1 accepts up to 10 reference images here." : state.mode === "krea" ? "Krea 2 Identity Edit accepts one image plus one optional reference." : "Voice cloning accepts one audio or video reference.");
    return;
  }
  if (savedReference) state.savedReferences.push(savedReference);
  else state.files = combined;
  if (incoming.length) markAiPromptStale();
  renderReferences();
  $("#reference-files").value = "";
  return true;
}

function shortName(name) {
  return name.split(/[\\/]/).pop().replace(/\.safetensors$/i, "");
}

function normalizedLoraName(name) {
  return String(name || "").replaceAll("\\", "/").toLowerCase();
}

function isQwen21Lora(name) {
  const normalized = normalizedLoraName(name);
  return normalized.startsWith("q21/") || normalized.includes("qwen21") ||
    normalized.includes("qwen_2.1") || normalized.includes("qwen-image") || normalized.includes("qwen_image");
}

function loraCompatible(name, mode = state.mode) {
  const normalized = normalizedLoraName(name);
  if (mode === "qwen") return isQwen21Lora(name) && name !== QWEN21_TURBO;
  if (mode === "krea") return /krea[_-]?2/.test(normalized) && name !== "krea2_identity_edit_v1_2.safetensors";
  if (mode === "h3") return !isQwen21Lora(name) && !/krea[_-]?2/.test(normalized);
  return false;
}

function selectedLoraEntries(mode = state.mode) {
  return [...state.selectedLoras.entries()].filter(([name]) => loraCompatible(name, mode));
}

function renderSelectedLoras() {
  scheduleDraftSave();
  const root = $("#selected-loras");
  root.replaceChildren();
  const selected = selectedLoraEntries();
  if (!selected.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = state.mode === "h3"
      ? "No extra LoRAs selected."
      : state.mode === "qwen" ? "No extra LoRAs selected. Choose installed Q21 LoRAs below; Viggle Turbo is controlled above." : "No extra LoRAs. Krea 2 Identity Edit v1.2 is always applied.";
    root.append(empty);
    return;
  }
  selected.forEach(([name, strength]) => {
    const row = document.createElement("div");
    row.className = "selected-lora";
    const title = document.createElement("strong");
    title.textContent = shortName(name);
    title.title = name;
    const strengthRow = document.createElement("label");
    strengthRow.className = "strength-row";
    strengthRow.append("Strength");
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "-2";
    slider.max = "2";
    slider.step = "0.05";
    slider.value = String(Math.max(-2, Math.min(2, Number(strength))));
    slider.setAttribute("aria-label", `${shortName(name)} strength slider`);
    const number = document.createElement("input");
    number.type = "number";
    number.className = "strength-number";
    number.step = "0.05";
    number.value = String(Number(strength));
    number.setAttribute("aria-label", `${shortName(name)} exact strength`);
    slider.addEventListener("input", () => {
      state.selectedLoras.set(name, Number(slider.value));
      number.value = Number(slider.value).toFixed(2);
    });
    number.addEventListener("input", () => {
      const value = Number(number.value);
      if (!Number.isFinite(value)) return;
      state.selectedLoras.set(name, value);
      slider.value = String(Math.max(Number(slider.min), Math.min(Number(slider.max), value)));
    });
    number.addEventListener("change", () => {
      const value = Number(number.value);
      if (Number.isFinite(value)) return;
      number.value = String(state.selectedLoras.get(name) ?? 0);
    });
    strengthRow.append(slider, number);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.selectedLoras.delete(name);
      renderSelectedLoras();
      renderLoraList();
    });
    row.append(title, remove, strengthRow);
    root.append(row);
  });
}

function renderLoraList() {
  const root = $("#lora-list");
  const query = $("#lora-search").value.trim().toLowerCase();
  const candidates = state.loras.filter((name) => {
    if ([...Object.values(TURBO_LORAS), ...Object.values(FRAME_TURBO_LORAS), QWEN21_TURBO].includes(name) || name === "krea2_identity_edit_v1_2.safetensors") return false;
    if (state.mode === "h3" && name === VSA_LORA) return false;
    const haystack = name.toLowerCase();
    const familyMatch = loraCompatible(name);
    if (state.mode === "qwen") return familyMatch && (!query || haystack.includes(query));
    return query ? haystack.includes(query) : familyMatch;
  }).slice(0, 100);
  root.replaceChildren();
  if (!candidates.length) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = state.loras.length ? "No matching LoRAs." : "No installed LoRAs found.";
    root.append(empty);
    return;
  }
  candidates.forEach((name) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "lora-option";
    const label = document.createElement("span");
    label.textContent = shortName(name);
    label.title = name;
    const marker = document.createElement("i");
    marker.textContent = state.selectedLoras.has(name) ? "✓" : "+";
    button.append(label, marker);
    button.addEventListener("click", () => {
      if (state.selectedLoras.has(name)) state.selectedLoras.delete(name);
      else state.selectedLoras.set(name, 0.75);
      renderSelectedLoras();
      renderLoraList();
    });
    root.append(button);
  });
}

function renderVoiceLoras() {
  const select = $("#voice-lora");
  const selected = select.value;
  select.replaceChildren(new Option("None", "None"));
  state.loras.forEach((name) => select.append(new Option(shortName(name), name)));
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
}

async function uploadReferences() {
  const files = [...state.files];
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    setStatus("Uploading references", `${index + 1} of ${files.length}`, Math.round((index / files.length) * 15));
    const saved = await uploadReferenceFile(file, state.mode === "ltx" ? "ltx" : `h3_${index}`);
    state.savedReferences.push({
      kind: isAudioFile(file) ? "audio" : file.type.startsWith("video/") ? "video" : "image",
      file: [saved.subfolder, saved.name].filter(Boolean).join("/"),
      ...(file.type.startsWith("video/") ? { use_soundtrack: true } : {}),
    });
    state.files = state.files.filter(pending => pending !== file);
    renderReferences();
  }
  // Silent video references must not be wired into Native Audio Lock.
  for (const reference of state.savedReferences) {
    if (state.mode !== "h3" || !$("#h3-audio-lock").checked || $("#h3-single-frame").checked || state.savedReferences.some(ref => ref.kind === "audio") || reference.kind !== "video" || reference.use_soundtrack === false) continue;
    const response = await fetch(`/api/v1/generator/probe?file=${encodeURIComponent(reference.file)}`);
    if (!response.ok) throw new Error("Could not check the reference video's soundtrack. Tap Generate again to retry.");
    const info = await response.json();
    reference.use_soundtrack = info.has_audio === true;
  }
  return state.savedReferences.map(reference => ({ ...reference }));
}

function sparseAttentionInputs(model) {
  if (!$("#sparse-enabled").checked) return null;
  const method = $("#sparse-method").value;
  if (!["sla", "vsa", "sol-attn"].includes(method)) throw new Error("Choose SLA, VSA or SOL sparse attention.");
  return {
    model, selection: method,
    ...(method === "sol-attn" ? {"selection.tau": 1.3} : {"selection.keep_percent": 10}),
    start_percent: 0.2, end_percent: 1, dense_blocks: "", min_tokens: 12288,
    extra_tokens: 256, sink_conditioning: "exact_kv_and_rows", verbose: false,
  };
}

function sparseAttentionDefaults() {
  const omni = state.h3Type !== "frames" && $("#h3-model").value === OMNI_MODEL;
  $("#sparse-enabled").checked = omni;
  $("#sparse-method").value = "vsa";
}

function buildH3Workflow(references, seed, creative = null, options = {}) {
  const frameMode = state.h3Type === "frames";
  const singleFrame = $("#h3-single-frame").checked;
  const turboSteps = Number(studioRoot.querySelector('input[name="turbo"]:checked').value);
  if (![4, 8].includes(turboSteps)) throw new Error("ACC presets are no longer supported. Choose Fast or Quality.");
  const eros = (frameMode ? $("#frame-h3-model").value : $("#h3-model").value) === EROSMAX_MODEL;
  const singularity = !frameMode && $('#h3-model').value === SINGULARITY_MODEL;
  const omni = !frameMode && $('#h3-model').value === OMNI_MODEL;
  if (singularity && ![4, 8].includes(turboSteps)) throw new Error('Singularity requires the 4-step or 8-step Ref2VA LoRA.');
  const turboEnabled = singularity || (!omni && !eros && $("#turbo-enabled").checked);
  const useErSde = eros;
  const steps = Number($("#total-steps").value);
  const megapixels = Number($("#megapixels").value);
  const duration = singleFrame ? 5 / 24 : Number($("#duration").value);
  const aspect = $("#aspect-ratio").value;
  const enhancePrompt = options.enhanceOverride ?? (state.runPrompt ? false : (creative ? false : $("#ai-enhance").checked));
  const promptText = options.promptOverride ?? state.runPrompt ?? $("#direction").value.trim();
  const workflow = {
    "119": { inputs: { vae_name: "minimax_h3_video_vae_int8_convrot.safetensors" }, class_type: "VAELoader" },
    "120": { inputs: { vae_name: "minimax_h3_audio_vae_fp32.safetensors" }, class_type: "VAELoader" },
    "121": { inputs: { samples: ["167", 0], vae: ["120", 0] }, class_type: "VAEDecodeAudio" },
    "122": { inputs: { samples: ["167", 0], vae: ["119", 0] }, class_type: "VAEDecode" },
    "124": { inputs: { scheduler: useErSde ? "beta57" : "simple", steps, denoise: 1, model: ["176", 0] }, class_type: "BasicScheduler" },
    "125": { inputs: { noise: ["129", 0], guider: ["126", 0], sampler: ["142", 0], sigmas: ["124", 0], latent_image: ["131", 1] }, class_type: "SamplerCustomAdvanced" },
    "126": { inputs: { model: ["176", 0], conditioning: ["131", 0] }, class_type: "BasicGuider" },
    "127": { inputs: { unet_name: frameMode ? $("#frame-h3-model").value : $("#h3-model").value, weight_dtype: "default" }, class_type: "UNETLoader" },
    "128": { inputs: { clip_name: H3_TEXT_ENCODER, type: "minimax", device: "default" }, class_type: "CLIPLoader" },
    "129": { inputs: { noise_seed: seed }, class_type: "RandomNoise" },
    "131": frameMode
      ? { inputs: { prompt: promptText, width: ["162", 0], height: ["162", 1], length: ["132", 1], clip: ["128", 0], vae: ["119", 0] }, class_type: "MiniMaxH3ImageToVideo" }
      : { inputs: { prompt: ["181", 18], width: ["162", 0], height: ["162", 1], length: ["132", 1], ref_image_size: "match", clip: ["128", 0], vae: ["119", 0], audio_vae: ["120", 0] }, class_type: "MiniMaxH3ReferenceToVideo" },
    "132": { inputs: { expression: "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17", "values.a": ["133", 0] }, class_type: "ComfyMathExpression" },
    "133": { inputs: { value: duration }, class_type: "PrimitiveFloat" },
    "142": { inputs: { sampler_name: useErSde ? "er_sde" : "euler" }, class_type: "KSamplerSelect" },
    "155": { inputs: { frame_rate: 24, loop_count: 0, filename_prefix: "H3/h3_mobile", format: "video/h264-mp4", pix_fmt: "yuv420p", crf: 18, save_metadata: true, trim_to_audio: false, pingpong: false, save_output: true, images: ["122", 0], audio: ["121", 0] }, class_type: "VHS_VideoCombine" },
    "162": { inputs: { aspect_ratio: aspect, megapixels, multiple: 32 }, class_type: "ResolutionSelector" },
    "167": { inputs: { value: ["125", 0] }, class_type: "UnloadAllModels" },
    "168": { inputs: { shift_video: 12, shift_audio: 3, model: ["145", 0] }, class_type: "MiniMaxH3SigmaShift" },
    "175": { inputs: { head_chunks: 4, model: ["177", 0] }, class_type: "MiniMaxLowVRAMAttention" },
    "176": { inputs: { attention: "comfy kitchen attention", model: ["175", 0] }, class_type: "ModelAttentionBackend" },
    "177": { inputs: { chunks: 8, seq_threshold: 4096, model: ["168", 0] }, class_type: "MiniMaxChunkFeedForward" },
  };

  if (singleFrame) {
    delete workflow["120"];
    delete workflow["121"];
    delete workflow["131"].inputs.audio_vae;
    workflow["122"] = { inputs: { samples: ["167", 0], vae: ["119", 0] }, class_type: "FizgigH3StillDecode" };
    workflow["125"].inputs.latent_image = ["154", 0];
    workflow["154"] = { inputs: { width: ["162", 0], height: ["162", 1], batch_size: 1 }, class_type: "FizgigH3StillLatent" };
    workflow["155"] = { inputs: { filename_prefix: "H3/h3_mobile_still", images: ["122", 0] }, class_type: "SaveImage" };
  }

  if (frameMode) {
    const roles = [];
    for (const [kind, nodeId] of [["first_frame", "183"], ["last_frame", "184"]]) {
      const reference = references.find((item) => item.kind === kind);
      if (!reference) continue;
      workflow[nodeId] = { inputs: { image: reference.file }, class_type: "LoadImage" };
      const cropId = kind === "first_frame" ? "187" : "188";
      workflow[cropId] = { inputs: { image: [nodeId, 0], upscale_method: "lanczos", width: ["162", 0], height: ["162", 1], crop: "center" }, class_type: "ImageScale" };
      workflow["131"].inputs[kind] = [cropId, 0];
      roles.push({ kind, nodeId: cropId });
    }
    if (enhancePrompt) {
      const context = roles.length
        ? roles.map(({ kind }, index) => `<Picture ${index + 1}> is the ${kind === "first_frame" ? "literal FIRST frame (opening)" : "literal LAST frame (closing)"} of the target video.`).join("\n")
        : "No frames are supplied. Generate the video entirely from the text request.";
      workflow["185"] = {
        inputs: {
          prompt: promptText,
          mode: "MiniMax H3 — First and Last Frame to Video",
          profile: state.promptDirectorProfile,
          creativity: "Balanced",
          width: ["162", 0],
          height: ["162", 1],
          duration: Math.max(4, Math.min(15, Math.round(duration))),
          seed: seed % 4294967296,
          max_attempts: 2,
          reference_context: context,
          prompt_provider: "openrouter",
          openrouter_api_key: "",
          openrouter_model: $("#ai-model").value,
          reasoning_effort: "none",
          system_prompt_source: "built-in for output target",
          fail_on_error: true,
        },
        class_type: "PromptDirector",
      };
      roles.forEach(({ nodeId }, index) => {
        workflow["185"].inputs[`reference_images.reference_image_${index}`] = [nodeId, 0];
      });
      workflow["131"].inputs.prompt = ["185", 0];
    }
  } else {
    workflow["181"] = { inputs: { direction: promptText, references_json: JSON.stringify({ references }), system_prompt: "", prompt_provider: enhancePrompt ? "openrouter" : "none", openrouter_api_key: "", openrouter_model: $("#ai-model").value, reasoning_effort: "none", api_base: "", local_model_slug: "", job_type: studioRoot.querySelector('input[name="job-type"]:checked').value, width: ["162", 0], height: ["162", 1], length_seconds: ["133", 0], max_reference_edge: 1280, disable_local_thinking: false }, class_type: "MiniMaxH3ReferencePack" };
  }

  if (enhancePrompt && !frameMode) {
    workflow["182"] = { inputs: { string: ["181", 18], regex_pattern: "\\bflush\\b", replace: "", case_insensitive: true }, class_type: "RegexReplace" };
    workflow["131"].inputs.prompt = ["182", 0];
  }

  if (!frameMode) {
    const imageRefs = references.filter((ref) => ref.kind === "image");
    const videoRefs = references.filter((ref) => ref.kind === "video");
    const audioRefs = references.filter((ref) => ref.kind === "audio");
    imageRefs.forEach((ref, index) => { workflow["131"].inputs[`ref_images.ref_image_${index}`] = preprocessReferenceImage(workflow, ["181", index], ref, index); });
    videoRefs.forEach((ref, index) => {
      workflow["131"].inputs[`ref_videos.ref_video_${index}`] = ["181", 9 + index];
      workflow["131"].inputs[`ref_video_audios.ref_video_audio_${index}`] = ["181", 12 + index];
    });
    audioRefs.forEach((ref, index) => { workflow["131"].inputs[`ref_audios.ref_audio_${index}`] = ["181", 15 + index]; });
  }

  let previous = "127";
  if (turboEnabled) {
    workflow["145"] = { inputs: { lora_name: (frameMode ? FRAME_TURBO_LORAS : TURBO_LORAS)[turboSteps], strength_model: frameMode ? Number($("#frame-turbo-strength").value) : 1, model: [previous, 0] }, class_type: "LoraLoaderModelOnly" };
    previous = "145";
  }
  const sparseInputs = sparseAttentionInputs([previous, 0]);
  if (sparseInputs) {
    workflow["146"] = { inputs: sparseInputs, class_type: "BlockSparseAttention" };
    previous = "146";
  }
  selectedLoraEntries("h3").filter(([name]) => name !== VSA_LORA).forEach(([name, strength], index) => {
    const id = String(200 + index);
    workflow[id] = { inputs: { lora_name: name, strength_model: strength, model: [previous, 0] }, class_type: "LoraLoaderModelOnly" };
    previous = id;
  });
  workflow["168"].inputs.model = [previous, 0];
  if (!frameMode && !singleFrame && $("#h3-audio-lock").checked) {
    const audioRefs = references.filter(ref => ref.kind === "audio");
    const videoRefs = references.filter(ref => ref.kind === "video");
    const soundtrackIndex = videoRefs.findIndex(ref => ref.use_soundtrack !== false);
    const audio = audioRefs.length ? ["181", 15] : soundtrackIndex >= 0 ? ["181", 12 + soundtrackIndex] : null;
    if (audio) {
      workflow["186"] = { inputs: { model: ["176", 0], av_latent: ["131", 1], audio_vae: ["120", 0], audio }, class_type: "MiniMaxH3NativeAudioLock" };
      workflow["124"].inputs.model = ["186", 0];
      workflow["126"].inputs.model = ["186", 0];
      workflow["125"].inputs.latent_image = ["186", 1];
      workflow["155"].inputs.audio = ["186", 2];
      delete workflow["121"];
    }
  }
  if (!enhancePrompt && (creative || state.restoredCreative)) workflow["131"].inputs.prompt = promptText;
  return workflow;
}

function buildKreaWorkflow(references, seed) {
  const aspect = $("#aspect-ratio").value;
  const megapixels = Number($("#megapixels").value);
  const workflow = {
    "29": { inputs: { filename_prefix: "krea2/krea2_identity_edit_mobile", images: ["54", 0] }, class_type: "SaveImage" },
    "53": { inputs: { seed, steps: 10, cfg: 1, sampler_name: "euler", scheduler: "simple", denoise: 1, model: ["79", 0], positive: ["84", 0], negative: ["85", 0], latent_image: ["82", 0] }, class_type: "KSampler" },
    "54": { inputs: { samples: ["53", 0], vae: ["57", 0] }, class_type: "VAEDecode" },
    "55": { inputs: { unet_name: "krea2_turbo_int8_convrot.safetensors", weight_dtype: "default" }, class_type: "UNETLoader" },
    "56": { inputs: { clip_name: "qwen3vl_4b_fp8_scaled.safetensors", type: "krea2", device: "default" }, class_type: "CLIPLoader" },
    "57": { inputs: { vae_name: "qwen_image_vae.safetensors" }, class_type: "VAELoader" },
    "71": { inputs: { lora_name: "krea2_identity_edit_v1_2.safetensors", strength_model: 1, model: ["55", 0] }, class_type: "LoraLoaderModelOnly" },
    "72": { inputs: { image: references[0].file }, class_type: "LoadImage" },
    "73": { inputs: { pixels: ["72", 0], vae: ["57", 0] }, class_type: "VAEEncode" },
    "79": { inputs: { model: ["71", 0], source_latent: ["73", 0], ref_boost: 4, ref_boost_a: 1, fit_mode: "fit", vae: ["57", 0], source_image: ["72", 0], target_latent: ["82", 0] }, class_type: "Krea2EditModelPatch" },
    "82": { inputs: { width: ["83", 0], height: ["83", 1], batch_size: 1 }, class_type: "EmptySD3LatentImage" },
    "83": { inputs: { aspect_ratio: aspect, megapixels, multiple: 8 }, class_type: "ResolutionSelector" },
    "84": { inputs: { clip: ["56", 0], prompt: $("#direction").value.trim(), image: ["72", 0], grounding_px: 768, system_prompt: "" }, class_type: "Krea2EditGroundedEncode" },
    "85": { inputs: { clip: ["56", 0], prompt: "", image: ["72", 0], grounding_px: 768, system_prompt: "" }, class_type: "Krea2EditGroundedEncode" },
  };

  const firstImage = preprocessReferenceImage(workflow, ["72", 0], references[0], 0);
  workflow["73"].inputs.pixels = firstImage;
  workflow["79"].inputs.source_image = firstImage;
  workflow["84"].inputs.image = firstImage;
  workflow["85"].inputs.image = firstImage;
  if (references[1]) {
    workflow["90"] = { inputs: { image: references[1].file }, class_type: "LoadImage" };
    const secondImage = preprocessReferenceImage(workflow, ["90", 0], references[1], 1);
    workflow["92"] = { inputs: { pixels: secondImage, vae: ["57", 0] }, class_type: "VAEEncode" };
    workflow["79"].inputs.source_latent_b = ["92", 0];
    workflow["79"].inputs.source_image_b = secondImage;
    workflow["84"].inputs.image_b = secondImage;
    workflow["85"].inputs.image_b = secondImage;
  }

  let previous = "71";
  selectedLoraEntries("krea").forEach(([name, strength], index) => {
    const id = String(200 + index);
    workflow[id] = { inputs: { lora_name: name, strength_model: strength, model: [previous, 0] }, class_type: "LoraLoaderModelOnly" };
    previous = id;
  });
  workflow["79"].inputs.model = [previous, 0];
  return workflow;
}

function normalizeAspect(aspect) {
  return ({ "2:3 (Portrait)": "2:3 (Portrait Photo)", "3:2 (Landscape)": "3:2 (Photo)" })[aspect] || aspect;
}

function qwenOutputSize() {
  const ratios = { "9:16 (Portrait Widescreen)": 9 / 16, "16:9 (Widescreen)": 16 / 9, "1:1 (Square)": 1, "2:3 (Portrait Photo)": 2 / 3, "3:4 (Portrait Standard)": 3 / 4, "3:2 (Photo)": 3 / 2, "4:3 (Standard)": 4 / 3 };
  const ratio = ratios[$("#aspect-ratio").value] || 1;
  const pixels = Math.max(0.25, Number($("#megapixels").value) || 1) * 1_000_000;
  return {
    width: Math.max(32, Math.round(Math.sqrt(pixels * ratio) / 32) * 32),
    height: Math.max(32, Math.round(Math.sqrt(pixels / ratio) / 32) * 32),
  };
}

function buildQwenWorkflow(references, seed) {
  const turbo = $("#qwen-turbo").checked;
  const size = qwenOutputSize();
  const workflow = {
    "1": { inputs: { unet_name: QWEN21_MODEL, weight_dtype: "default" }, class_type: "UNETLoader" },
    "2": { inputs: { clip_name: QWEN21_CLIP, type: "qwen_image", device: "default" }, class_type: "CLIPLoader" },
    "3": { inputs: { vae_name: QWEN21_VAE }, class_type: "VAELoader" },
    "4": { inputs: { clip: ["2", 0], prompt: state.runPrompt ?? $("#direction").value.trim(), negative_prompt: turbo ? "" : $("#qwen-negative").value.trim(), vae: ["3", 0], resolution: Number($("#qwen-reference-resolution").value) }, class_type: "TextEncodeQwenImage21" },
    "5": { inputs: { width: size.width, height: size.height, batch_size: 1 }, class_type: "EmptyLatentImage" },
    "6": { inputs: { model: ["1", 0], device: $("#qwen-cache").value === "off" ? "off" : "auto", dtype: $("#qwen-cache").value === "off" ? "default" : $("#qwen-cache").value }, class_type: "QwenImage21Cache" },
    "7": { inputs: { samples: [turbo ? "15" : "9", 0], vae: ["3", 0] }, class_type: "VAEDecode" },
    "8": { inputs: { filename_prefix: "qwen21/pocket_qwen21", images: ["7", 0] }, class_type: "SaveImage" },
  };
  references.forEach((reference, index) => {
    const id = String(20 + index);
    workflow[id] = { inputs: { image: reference.file }, class_type: "LoadImage" };
    workflow["4"].inputs[`images.image_${index + 1}`] = preprocessReferenceImage(workflow, [id, 0], reference, index);
  });
  const useSelectedCanvas = references.length && $("#qwen-edit-canvas").value === "selected";
  const latent = references.length && !useSelectedCanvas ? ["4", 2] : ["5", 0];
  let model = ["1", 0];
  selectedLoraEntries("qwen").forEach(([name, strength], index) => {
    const id = String(40 + index);
    workflow[id] = { inputs: { lora_name: name, strength_model: strength, model }, class_type: "LoraLoaderModelOnly" };
    model = [id, 0];
  });
  if (turbo) {
    workflow["10"] = { inputs: { model, lora_name: QWEN21_TURBO, strength: 1 }, class_type: "ViggleTurboLora" };
    workflow["6"].inputs.model = ["10", 0];
    workflow["11"] = { inputs: { noise_seed: seed }, class_type: "RandomNoise" };
    workflow["12"] = { inputs: { model: ["6", 0], conditioning: ["4", 0] }, class_type: "BasicGuider" };
    workflow["13"] = { inputs: { sampler_name: "euler" }, class_type: "KSamplerSelect" };
    workflow["14"] = { inputs: { latent, nodes: "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25" }, class_type: "ViggleTurboSigmas" };
    workflow["15"] = { inputs: { noise: ["11", 0], guider: ["12", 0], sampler: ["13", 0], sigmas: ["14", 0], latent_image: latent }, class_type: "SamplerCustomAdvanced" };
  } else {
    workflow["6"].inputs.model = model;
    workflow["9"] = { inputs: { seed, steps: Number($("#qwen-steps").value), cfg: Number($("#qwen-cfg").value), sampler_name: "euler", scheduler: "simple", denoise: 1, model: ["6", 0], positive: ["4", 0], negative: ["4", 1], latent_image: latent }, class_type: "KSampler" };
  }
  return workflow;
}

function buildFaceRefineWorkflow(references, seed) {
  const video = references.find((ref) => ref.kind === "video");
  const face = references.find((ref) => ref.kind === "image");
  const model = $("#face-model").value;
  const canvas = Number($("#face-canvas").value);
  const seconds = Math.max(0, Number($("#face-max-seconds").value) || 0);
  const workflow = {
    // Loaded at 24 fps so frames, H3's lipsync audio and the 24 fps output stay aligned.
    "401": { inputs: { video: video.file, force_rate: 24, custom_width: 0, custom_height: 0, frame_load_cap: Math.round(seconds * 24), skip_first_frames: 0, select_every_nth: 1, format: "None" }, class_type: "VHS_LoadVideo" },
    "402": { inputs: { image: face.file }, class_type: "LoadImage" },
    "403": { inputs: { unet_name: model, weight_dtype: "default" }, class_type: "UNETLoader" },
    "404": { inputs: { clip_name: H3_TEXT_ENCODER, type: "minimax", device: "default" }, class_type: "CLIPLoader" },
    "405": { inputs: { vae_name: "minimax_h3_video_vae_int8_convrot.safetensors" }, class_type: "VAELoader" },
    "406": { inputs: { vae_name: "minimax_h3_audio_vae_fp32.safetensors" }, class_type: "VAELoader" },
    "408": { inputs: { images: ["401", 0], detector: "face_yolov8m.pt", confidence: 0.35, crop_factor: 3, canvas_width: canvas, canvas_height: canvas, canvas_mode: "manual", smooth_window: 21, size_smooth_window: 51, smooth_method: "gaussian", size_mode: "per_frame", identity_track: false, identity_threshold: 0.28, select: "largest_face", fallback_detector: "none", fallback_head_frac: 0.5, select_index: 0, identity_model: "insightface", cut_detection: "none", cut_threshold: 3, absent_shots: "off", X: 0, Y: 0, frame_index: 0 }, class_type: "H3FaceTrackCrop" },
    "409": { inputs: { prompt: FACE_REFINE_PROMPT, width: ["408", 4], height: ["408", 5], length: ["408", 6], ref_image_size: "match", clip: ["404", 0], vae: ["405", 0], audio_vae: ["406", 0], "ref_images.ref_image_0": ["402", 0], "ref_audios.ref_audio_0": ["401", 2] }, class_type: "MiniMaxH3ReferenceToVideo" },
    "410": { inputs: { av_latent: ["409", 1], images: ["408", 0], vae: ["405", 0] }, class_type: "H3InjectVideoLatent" },
    "411": { inputs: { model: ["403", 0], av_latent: ["410", 0], audio_vae: ["406", 0], audio: ["401", 2] }, class_type: "MiniMaxH3NativeAudioLock" },
    "412": { inputs: { model: ["411", 0], av_latent: ["411", 1], transform: ["408", 1], denoise_multiplier_small_face: 1, denoise_multiplier_large_face: 0.35, scale_mode: "absolute_px", face_px_small: 30, face_px_large: 120, gamma: 1, smooth_frames: 9 }, class_type: "H3PerFrameDenoise" },
    "413": { inputs: { model: ["412", 2], conditioning: ["409", 0] }, class_type: "BasicGuider" },
    "414": { inputs: { sampler_name: model === EROSMAX_MODEL ? "er_sde" : "euler" }, class_type: "KSamplerSelect" },
    "415": { inputs: { scheduler: model === EROSMAX_MODEL ? "beta57" : "simple", steps: 8, denoise: Number($("#face-denoise").value), model: ["412", 2] }, class_type: "BasicScheduler" },
    "416": { inputs: { noise_seed: seed }, class_type: "RandomNoise" },
    "417": { inputs: { noise: ["416", 0], guider: ["413", 0], sampler: ["414", 0], sigmas: ["415", 0], latent_image: ["412", 0] }, class_type: "SamplerCustomAdvanced" },
    "418": { inputs: { samples: ["417", 0], vae: ["405", 0] }, class_type: "VAEDecode" },
    "419": { inputs: { base_images: ["401", 0], refined_crops: ["418", 0], transform: ["408", 1], paste_region: "face_only", mask_dilation: 24, feather: 24, colour_match: 1, blend: 1, undetected_frames: "fade_out", feather_scales_with_crop: false }, class_type: "H3FaceStitch" },
    "420": { inputs: { frame_rate: 24, loop_count: 0, filename_prefix: "FaceRefine/pocket_face_refine", format: "video/h264-mp4", pix_fmt: "yuv420p", crf: 16, save_metadata: true, trim_to_audio: false, pingpong: false, save_output: true, images: ["419", 0], audio: ["401", 2] }, class_type: "VHS_VideoCombine" },
  };
  if (model === EROSMAX_MODEL) {
    workflow["407"] = { inputs: { gate_file: VSA_LORA, sparsity: 0.75, model: ["403", 0] }, class_type: "Ref2VAVSAGatePatch" };
    workflow["411"].inputs.model = ["407", 0];
  } else {
    workflow["407"] = { inputs: { lora_name: FACE_REFINE_TURBO, strength_model: 1, model: ["403", 0] }, class_type: "LoraLoaderModelOnly" };
    workflow["411"].inputs.model = ["407", 0];
  }
  return workflow;
}

function captureFaceRecipe(references, seed) {
  return {
    mode: "faces", direction: "Face Refine", references, seed,
    baseModel: $("#face-model").value, denoise: Number($("#face-denoise").value),
    canvas: Number($("#face-canvas").value), maxSeconds: Number($("#face-max-seconds").value) || 0,
    steps: 8, renderTarget: $("#render-target").value,
  };
}

function buildVoiceWorkflow(references) {
  const workflow = {
    "300": { inputs: { min_tokens: 10, max_tokens: 4096, temperature: 0.8, sway: 1, use_cfg_zero_star: true, retry_max_attempts: 3, retry_threshold: 6 }, class_type: "VoxCPM_AdvancedParams" },
    "301": { inputs: { model_name: "VoxCPM2", lora_name: $("#voice-lora").value, text: $("#direction").value.trim(), voice_design: $("#voice-design").value.trim(), cfg: Number($("#voice-cfg").value), steps: Number($("#voice-steps").value), normalize_text: true, seed: Math.floor(Math.random() * Number.MAX_SAFE_INTEGER), force_offload: false, device: "cuda", dtype: "auto", advanced_params: ["300", 0] }, class_type: "VoxCPM_TTS" },
    "302": { inputs: { audio: ["301", 0], filename_prefix: "voice/voxcpm2_mobile", quality: "320k" }, class_type: "SaveAudioMP3" },
  };
  if (studioRoot.querySelector('input[name="voice-mode"]:checked').value === "clone") {
    workflow["303"] = { inputs: { audio: references[0].file }, class_type: "LoadAudio" };
    let source = ["303", 0];
    if ($("#clean-voice").checked) {
      workflow["304"] = { inputs: { audio: source, backend: $("#denoise-backend").value, df_atten_db: 100, re_mode: "denoise_and_enhance", re_nfe: 32, re_lambd: 0.9 }, class_type: "SupaVoiceDenoiser" };
      source = ["304", 0];
    }
    workflow["305"] = { inputs: { reference_audio: source, trim_silence: false, prompt_text: "" }, class_type: "VoxCPM_VoiceCloning" };
    workflow["301"].inputs.voice_config = ["305", 0];
  }
  return workflow;
}

function buildWorkflow(references, seed) {
  if (state.mode === "h3") return buildH3Workflow(references, seed);
  if (state.mode === "qwen") return buildQwenWorkflow(references, seed);
  if (state.mode === "krea") return buildKreaWorkflow(references, seed);
  if (state.mode === "faces") return buildFaceRefineWorkflow(references, seed);
  return buildVoiceWorkflow(references);
}

function findOutputMedia(history, mode) {
  const entries = Object.values(history || {});
  const outputs = entries[0]?.outputs || {};
  for (const node of Object.values(outputs)) {
    for (const value of Object.values(node || {})) {
      if (!Array.isArray(value)) continue;
      const extensions = ["faces", "ltx"].includes(mode) ? /\.(mp4|webm|mov|mkv)$/i : mode === "h3" ? /\.(mp4|webm|mov|mkv|png|jpe?g|webp)$/i : ["krea", "qwen"].includes(mode) ? /\.(png|jpe?g|webp)$/i : /\.(mp3|wav|flac|ogg|m4a)$/i;
      const file = value.find((item) => item && typeof item === "object" && item.filename && extensions.test(item.filename));
      if (file) return file;
    }
  }
  return null;
}

function normalizeRecipe(recipe) {
  if (!recipe) return recipe;
  const result = { ...recipe };
  if (result.creative && Object.hasOwn(result.creative, "brief")) {
    const legacy = !Object.hasOwn(result, "sourceDirection") || result.direction !== result.sourceDirection;
    const original = legacy ? result.creative.brief : result.sourceDirection;
    const final = legacy ? result.direction : result.aiPrompt;
    Object.assign(result, { direction: original, sourceDirection: original, aiPrompt: final || "", enhancePrompt: legacy ? false : result.enhancePrompt ?? false });
  } else if (Object.hasOwn(result, "sourceDirection")) result.direction = result.sourceDirection;
  return result;
}

function recipePriority(recipe, fromGraph) {
  if (!["h3", "qwen", "faces", "krea", "ltx", "voice"].includes(recipe?.mode)) return -1;
  if (fromGraph) return 0;
  if (Object.hasOwn(recipe, "sourceDirection")) return recipe.sourceDirection ? 3 : 0.5;
  if (recipe.creative?.brief) return 2;
  return 1;
}

function recipeFromHistoryEntry(entry) {
  const prompt = entry?.prompt?.[2];
  if (prompt?.["408"]?.class_type === "H3FaceTrackCrop") {
    return {
      mode: "faces", direction: "Face Refine",
      references: [{ kind: "video", file: prompt["401"]?.inputs?.video }, { kind: "image", file: prompt["402"]?.inputs?.image }],
      seed: prompt["416"]?.inputs?.noise_seed, baseModel: prompt["403"]?.inputs?.unet_name,
      denoise: prompt["415"]?.inputs?.denoise, canvas: prompt["408"]?.inputs?.canvas_width,
      maxSeconds: (prompt["401"]?.inputs?.frame_load_cap || 0) / 24, steps: 8,
    };
  }
  if (prompt?.["4"]?.class_type === "TextEncodeQwenImage21") {
    const references = Object.entries(prompt)
      .filter(([, node]) => node.class_type === "LoadImage")
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([, node]) => ({ kind: "image", file: node.inputs.image }));
    const turbo = prompt["10"]?.class_type === "ViggleTurboLora";
    const loras = Object.entries(prompt)
      .filter(([, node]) => node.class_type === "LoraLoaderModelOnly")
      .map(([, node]) => ({ name: node.inputs.lora_name, strength: node.inputs.strength_model }));
    const width = prompt["5"]?.inputs?.width, height = prompt["5"]?.inputs?.height;
    const latentInput = turbo ? prompt["15"]?.inputs?.latent_image : prompt["9"]?.inputs?.latent_image;
    const aspect = (() => {
      if (!width || !height) return "1:1 (Square)";
      const actual = width / height;
      const ratios = [["9:16 (Portrait Widescreen)", 9 / 16], ["2:3 (Portrait Photo)", 2 / 3], ["3:4 (Portrait Standard)", 3 / 4], ["1:1 (Square)", 1], ["4:3 (Standard)", 4 / 3], ["3:2 (Photo)", 3 / 2], ["16:9 (Widescreen)", 16 / 9]];
      return ratios.reduce((best, candidate) => Math.abs(candidate[1] - actual) < Math.abs(best[1] - actual) ? candidate : best)[0];
    })();
    return {
      mode: "qwen", direction: "", sourceDirection: "", aiPrompt: prompt["4"].inputs.prompt, enhancePrompt: false, negative: prompt["4"].inputs.negative_prompt || "",
      references, turbo, steps: turbo ? 6 : prompt["9"]?.inputs?.steps, cfg: turbo ? 1 : prompt["9"]?.inputs?.cfg,
      seed: turbo ? prompt["11"]?.inputs?.noise_seed : prompt["9"]?.inputs?.seed,
      megapixels: width && height ? width * height / 1_000_000 : 1,
      aspect,
      editCanvas: references.length && latentInput?.[0] === "5" ? "selected" : "reference",
      referenceResolution: prompt["4"].inputs.resolution, cache: prompt["6"]?.inputs?.device === "off" ? "off" : prompt["6"]?.inputs?.dtype || "default", loras,
    };
  }
  const frameMode = prompt?.["131"]?.class_type === "MiniMaxH3ImageToVideo";
  if (!frameMode && prompt?.["181"]?.class_type !== "MiniMaxH3ReferencePack") return null;
  let references = [];
  if (frameMode) {
    for (const [kind, id] of [["first_frame", "183"], ["last_frame", "184"]]) {
      if (prompt[id]?.inputs?.image) references.push({ kind, file: prompt[id].inputs.image });
    }
  } else {
    try {
      references = JSON.parse(prompt["181"].inputs.references_json || "{}").references || [];
    } catch {
      return null;
    }
  }
  const turboEnabled = Boolean(prompt["145"]);
  const singleFrame = prompt["155"]?.class_type === "SaveImage" && prompt["154"]?.class_type === "ImageFromBatch";
  const turboName = String(prompt["145"]?.inputs?.lora_name || "");
  const turbo = turboName.includes("-Acc-8Step") ? 10 : turboName.includes("4step") || (!turboEnabled && Number(prompt["124"]?.inputs?.steps) <= 5) ? 4 : 8;
  const loras = Object.entries(prompt)
    .filter(([id, node]) => Number(id) >= 200 && node.class_type === "LoraLoaderModelOnly")
    .map(([, node]) => ({ name: node.inputs.lora_name, strength: node.inputs.strength_model }));
  const writer = frameMode ? prompt["185"]?.inputs : prompt["181"]?.inputs;
  const hasWriter = writer?.prompt_provider === "openrouter";
  const original = hasWriter ? (frameMode ? writer.prompt : writer.direction) : "";
  const literal = prompt["131"]?.inputs?.prompt;
  const final = typeof literal === "string" ? literal : !hasWriter && !frameMode ? writer?.direction : "";
  return {
    mode: "h3",
    h3Type: frameMode ? "frames" : "reference",
    direction: original, sourceDirection: original, aiPrompt: final || "",
    references,
    turbo,
    turboEnabled,
    sparseEnabled: Boolean(prompt["146"]),
    sparseMethod: prompt["146"]?.inputs?.selection || "vsa",
    singleFrame,
    audioLock: Object.values(prompt).some(node => node.class_type === "MiniMaxH3NativeAudioLock"),
    turboStrength: prompt["145"]?.inputs?.strength_model ?? 0.75,
    baseModel: prompt["127"]?.inputs?.unet_name || LEGACY_H3_MODEL,
    steps: prompt["124"]?.inputs?.steps,
    megapixels: prompt["162"]?.inputs?.megapixels,
    duration: singleFrame ? 5 : prompt["133"]?.inputs?.value,
    aspect: prompt["162"]?.inputs?.aspect_ratio,
    seed: prompt["129"]?.inputs?.noise_seed,
    enhancePrompt: hasWriter,
    aiModel: frameMode ? prompt["185"]?.inputs.openrouter_model : prompt["181"].inputs.openrouter_model,
    jobType: frameMode ? "standard" : prompt["181"].inputs.job_type,
    loras,
  };
}

function referenceThumbnail(recipe) {
  const reference = recipe?.references?.find((item) => item.kind === "first_frame" || item.kind === "image") || recipe?.references?.[0];
  if (!reference?.file) return null;
  const parts = reference.file.split("/");
  return { filename: parts.pop(), subfolder: parts.join("/"), type: "input", kind: reference.kind };
}

function historyTiming(entry) {
  const messages = entry.status?.messages || [];
  const start = messages.find(([kind]) => kind === "execution_start")?.[1]?.timestamp;
  const end = messages.find(([kind]) => kind === "execution_success")?.[1]?.timestamp;
  return { generation_seconds: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, (end - start) / 1000) : undefined };
}

function recentJobs(serverFiles, history, queue) {
  const jobs = [];
  const addQueued = (item, status) => {
    const recipe = recipeFromHistoryEntry({ prompt: item });
    if (!recipe) return;
    jobs.push({ prompt_id: item[1], status, recipe, fromGraph: true, thumbnailFile: referenceThumbnail(recipe), modified: item[0] });
  };
  (queue.queue_running || []).forEach((item) => addQueued(item, "Running"));
  (queue.queue_pending || []).forEach((item) => addQueued(item, "Queued"));
  Object.values(history || {})
    .sort((left, right) => (right.prompt?.[0] || 0) - (left.prompt?.[0] || 0))
    .forEach((entry) => {
      const recipe = recipeFromHistoryEntry(entry);
      const output = recipe ? findOutputMedia({ entry }, "h3") : null;
      if (!output) return;
      const thumbnail = output.workflow
        ? { filename: output.workflow, subfolder: output.subfolder || "", type: output.type || "output", kind: "image" }
        : referenceThumbnail(recipe);
      const timestamp = entry.status?.messages?.find(([kind]) => kind === "execution_start")?.[1]?.timestamp;
      jobs.push({ ...output, prompt_id: entry.prompt[1], status: "Finished", recipe, fromGraph: true, timings: historyTiming(entry), thumbnailFile: thumbnail, modified: timestamp ? timestamp / 1000 : 0 });
    });
  serverFiles.forEach((file) => jobs.push({ ...file, modified: file.modified > 1e12 ? file.modified / 1000 : file.modified, status: file.status || (file.filename ? "Finished" : "Saved preset"), thumbnailFile: file.thumbnail
    ? { filename: file.thumbnail, subfolder: file.subfolder || "", type: file.type || "output", kind: "image" }
    : referenceThumbnail(file.recipe) }));

  const unique = [];
  const promptIds = new Map();
  jobs.forEach((job) => {
    const aliases = [job.prompt_id, job.comfy_prompt_id].filter(Boolean);
    const existing = aliases.map(id => promptIds.get(id)).find(Boolean);
    if (existing) {
      // A recipe read back from the executed graph only knows the final (often AI) prompt.
      // The stored recipe keeps the original brief and the AI prompt separately, so it wins.
      if (recipePriority(job.recipe, job.fromGraph) > recipePriority(existing.recipe, existing.fromGraph)) {
        existing.recipe = normalizeRecipe(job.recipe);
        existing.fromGraph = false;
      }
      if (job.job_id) {
        for (const key of ["prompt_id", "job_id", "task_id", "comfy_prompt_id", "worker_id", "filename", "subfolder", "type", "url", "status", "progress", "message"]) {
          if (job[key] !== undefined) existing[key] = job[key];
        }
      }
      existing.url ||= job.url;
      existing.saved ||= Boolean(job.saved);
      existing.thumbnailFile ||= job.thumbnailFile;
      existing.preset_name ||= job.preset_name;
      existing.timings = { ...existing.timings, ...job.timings };
      existing.modified = Math.max(existing.modified || 0, job.modified || 0);
      aliases.forEach(id => promptIds.set(id, existing));
      return;
    }
    job.recipe = normalizeRecipe(job.recipe);
    aliases.forEach(id => promptIds.set(id, job));
    unique.push(job);
  });
  return unique;
}

function mediaQuery(file) {
  const query = new URLSearchParams({filename: file.filename, subfolder: file.subfolder || "", type: file.type || "output"});
  if (file.task_id && file.url?.startsWith("/outputs/")) query.set("task_id", file.task_id);
  else {
    const cloud = String(file.url || "").match(/^\/(?:h3-mobile\/dispatch-output|outputs)\/([^/]+)\//);
    if (cloud) query.set("task_id", decodeURIComponent(cloud[1]));
  }
  return query;
}

function previewUrl(file) { return `/api/v1/media/preview?${mediaQuery(file)}`; }

function mediaUrl(file) {
  if (file.url) return file.url;
  if (state.controllerMedia) return `/api/v1/media/file?${mediaQuery(file)}`;
  const query = new URLSearchParams({ filename: file.filename, subfolder: file.subfolder || "", type: file.type || "output" });
  return `/api/v1/media/file?${query}`;
}

async function shareMedia(file) {
  if (!file) return;
  try {
    const response = await fetch(mediaUrl(file));
    if (!response.ok) throw new Error("Could not open the saved output.");
    const blob = await response.blob();
    const sharedFile = new File([blob], file.filename, { type: blob.type || "application/octet-stream" });
    if (!navigator.share || (navigator.canShare && !navigator.canShare({ files: [sharedFile] }))) {
      throw new Error("Sharing files is not supported here. Open this page over HTTPS, then try again.");
    }
    await navigator.share({ files: [sharedFile], title: file.filename });
  } catch (error) {
    if (error.name === "AbortError") return;
    toast(error.message);
    showError(`${error.message}\nOn iPhone, use the HTTPS Tensor Master address. In the share sheet choose Save Image or Save Video.`);
  }
}

async function saveRecipe(promptId, payload) {
  const response = await fetch("/api/v1/generator/presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, ...payload }),
  });
  if (!response.ok) throw new Error("Could not save the reusable setup.");
}

function savedSetups() {
  try {
    const setups = JSON.parse(localStorage.getItem(SAVED_SETUPS_KEY) || "[]");
    return Array.isArray(setups) ? setups : [];
  } catch {
    return [];
  }
}

function rememberSetup(file) {
  const setups = savedSetups().filter((setup) => setup.prompt_id !== file.prompt_id);
  setups.unshift({
    prompt_id: file.prompt_id,
    filename: file.filename,
    subfolder: file.subfolder,
    type: file.type,
    thumbnail: file.workflow || file.thumbnail,
    modified: file.modified || Date.now() / 1000,
    url: file.url,
    timings: file.timings,
    preset_name: file.preset_name,
    status: file.status || "Saved",
    recipe: file.recipe,
    saved: true,
  });
  try {
    localStorage.setItem(SAVED_SETUPS_KEY, JSON.stringify(setups.slice(0, 100)));
  } catch {
    // The server copy still saves when browser storage is unavailable.
  }
}

function captureH3Recipe(references, seed) {
  return {
    ...(state.restoredCreative ? {creative: state.restoredCreative} : {}),
    mode: "h3",
    h3Type: state.h3Type,
    direction: $("#direction").value.trim(),
    sourceDirection: $("#direction").value.trim(),
    aiPrompt: $("#ai-generated-prompt").value.trim(),
    references,
    turbo: Number(studioRoot.querySelector('input[name="turbo"]:checked').value),
    turboEnabled: $("#turbo-enabled").checked && (state.h3Type === "frames" ? $("#frame-h3-model").value : $("#h3-model").value) !== EROSMAX_MODEL,
    sparseEnabled: $("#sparse-enabled").checked,
    sparseMethod: $("#sparse-method").value,
    turboStrength: Number($("#frame-turbo-strength").value),
    baseModel: state.h3Type === "frames" ? $("#frame-h3-model").value : $("#h3-model").value,
    steps: Number($("#total-steps").value),
    singleFrame: $("#h3-single-frame").checked,
    audioLock: $("#h3-audio-lock").checked,
    megapixels: Number($("#megapixels").value),
    duration: Number($("#duration").value),
    aspect: $("#aspect-ratio").value,
    seed,
    enhancePrompt: $("#ai-enhance").checked,
    aiModel: $("#ai-model").value,
    jobType: studioRoot.querySelector('input[name="job-type"]:checked').value,
    loras: selectedLoraEntries("h3").filter(([name]) => name !== VSA_LORA).map(([name, strength]) => ({ name, strength })),
  };
}

function captureQwenRecipe(references, seed) {
  return {
    mode: "qwen", direction: $("#direction").value.trim(), aiPrompt: $("#ai-generated-prompt").value.trim(), negative: $("#qwen-negative").value.trim(), references,
    sourceDirection: $("#direction").value.trim(),
    enhancePrompt: $("#qwen-enhance").checked, aiModel: $("#qwen-ai-model").value,
    turbo: $("#qwen-turbo").checked, steps: Number($("#qwen-steps").value), cfg: Number($("#qwen-cfg").value), seed,
    megapixels: Number($("#megapixels").value), aspect: $("#aspect-ratio").value,
    referenceResolution: Number($("#qwen-reference-resolution").value), cache: $("#qwen-cache").value,
    editCanvas: $("#qwen-edit-canvas").value,
    renderTarget: $("#render-target").value,
    loras: selectedLoraEntries("qwen").map(([name, strength]) => ({ name, strength })),
  };
}

function loadRecipe(recipe) {
  recipe = normalizeRecipe(recipe);
  if (recipe?.mode === "h3" && Number(recipe.turbo) === 10) {
    toast("ACC presets are no longer supported. Choose Fast or Quality in Generate.");
    return false;
  }
  if (!recipe || !["h3", "qwen", "faces", "krea", "ltx", "voice"].includes(recipe.mode)) return;
  $('#ai-prompt-only').checked = false;
  if(recipe.mode === 'voice') {
    setMode('voice');$('#direction').value = recipe.direction || '';
    state.savedReferences = (recipe.references || []).map(ref=>({...ref}));
    $('#voice-design').value = recipe.voiceDesign || '';
    $('#voice-cfg').value = recipe.voiceCfg ?? 2.5;
    $('#voice-steps').value = recipe.voiceSteps ?? 25;
    $('#voice-lora').value = recipe.voiceLora || 'None';
    studioRoot.querySelector(`input[name="voice-mode"][value="${recipe.voiceMode || 'tts'}"]`)?.click();
    $('#clean-voice').checked = Boolean(recipe.cleanVoice);$('#denoise-backend').value = recipe.denoiseBackend || 'Resemble Enhance';
    syncVoiceMode();renderReferences();return;
  }
  if (["ltx", "krea"].includes(recipe.mode)) {
    setMode(recipe.mode);
    if (recipe.mode === "ltx") {
      state.savedReferences = [{kind: "video", file: recipe.sourceFile}];
      $("#ltx-operation").value = recipe.ltxType || "upscale";
      $("#ltx-prompt").value = recipe.sourceDirection || recipe.direction || "";
      $("#ltx-start").value = recipe.start || 0;
      $("#ltx-duration").value = recipe.requestedDuration || recipe.duration;
      $("#ltx-seed").value = recipe.seed ?? 1;
    } else {
      state.savedReferences = (recipe.references || []).map(ref => ({...ref}));
      $("#direction").value = recipe.sourceDirection || recipe.direction || "";
      $("#megapixels").value = recipe.megapixels || 1;
      $("#aspect-ratio").value = recipe.aspect || "1:1 (Square)";
      state.selectedLoras = new Map((recipe.loras || []).map(lora => [lora.name, lora.strength]));
      renderSelectedLoras();
      state.kreaSeed = recipe.seed;
    }
    $("#render-target").value = poolTarget(recipe.renderTarget);
    renderReferences();
    setStatus("Setup restored", "Review the settings, then submit to the worker pool.", null);
    return;
  }
  if (recipe.mode === "faces") {
    setMode("faces");
    state.files = [];
    state.savedReferences = (recipe.references || []).filter((ref) => ref?.file).map((ref) => ({ ...ref }));
    $("#face-model").value = recipe.baseModel || EROSMAX_MODEL;
    $("#face-denoise").value = recipe.denoise ?? 0.5;
    $("#face-canvas").value = String(recipe.canvas || 512);
    $("#face-max-seconds").value = recipe.maxSeconds ?? 0;
    $("#face-seed").value = recipe.seed ?? -1;
    if (recipe.renderTarget) $("#render-target").value = recipe.renderTarget;
    renderReferences();
    setStatus("Setup restored", "Adjust strength or model, then refine again.", null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    toast("Face Refine setup restored.");
    return;
  }
  if (recipe.mode === "qwen") {
    window.creativeMaster?.single();
    setMode("qwen");
    state.files = [];
    state.savedReferences = Array.isArray(recipe.references) ? recipe.references.map((reference) => ({ ...reference })) : [];
    state.selectedLoras = new Map((recipe.loras || []).map((lora) => [lora.name, Number(lora.strength)]));
    $("#direction").value = recipe.sourceDirection ?? recipe.direction ?? "";
    state.aiPrompts.qwen = recipe.aiPrompt || "";
    state.aiPromptSources.qwen = recipe.sourceDirection ?? recipe.direction ?? "";
    $("#ai-generated-prompt").value = state.aiPrompts.qwen;
    $("#ai-generated-panel").open = Boolean(state.aiPrompts.qwen);
    $("#qwen-enhance").checked = recipe.enhancePrompt !== false;
    $("#qwen-ai-model").value = recipe.aiModel || $("#qwen-ai-model").value;
    $("#qwen-negative").value = recipe.negative || "";
    $("#qwen-turbo").checked = recipe.turbo === true;
    $("#qwen-steps").value = recipe.steps ?? (recipe.turbo === true ? 6 : 25);
    $("#qwen-steps").dataset.nativeSteps = String(recipe.turbo === true ? 25 : (recipe.steps ?? 25));
    $("#qwen-steps").dataset.turboActive = String(recipe.turbo === true);
    $("#qwen-cfg").value = recipe.cfg ?? 1;
    $("#qwen-seed").value = recipe.seed ?? -1;
    $("#megapixels").value = recipe.megapixels ?? 1;
    $("#aspect-ratio").value = normalizeAspect(recipe.aspect || "1:1 (Square)");
    $("#qwen-reference-resolution").value = String(recipe.referenceResolution ?? 1024);
    $("#qwen-edit-canvas").value = recipe.editCanvas === "selected" ? "selected" : "reference";
    $("#qwen-cache").value = recipe.cache || "default";
    if (recipe.renderTarget) $("#render-target").value = recipe.renderTarget;
    renderReferences(); renderSelectedLoras(); renderLoraList(); syncQwenTurbo();
    syncQwenEnhancement();
    setStatus("Setup restored", "Edit the prompt or settings, then run Qwen Image 2.1 again.", null);
    addLog(`Restored Qwen Image 2.1 setup with seed ${recipe.seed ?? "random"}.`, "success");
    window.scrollTo({ top: 0, behavior: "smooth" });
    toast("Qwen Image 2.1 setup restored.");
    return;
  }
  window.creativeMaster?.single();
  state.restoredCreative = recipe.creative || null;
  setMode("h3");
  setH3Type(recipe.h3Type || "reference");
  state.files = [];
  state.savedReferences = state.h3Type === "reference" && Array.isArray(recipe.references) ? recipe.references.map((reference) => ({ ...reference })) : [];
  state.frameFiles = { first_frame: null, last_frame: null };
  state.savedFrameRefs = state.h3Type === "frames" ? Object.fromEntries((recipe.references || []).filter((ref) => ["first_frame", "last_frame"].includes(ref.kind)).map((ref) => [ref.kind, { ...ref }])) : {};
  state.selectedLoras = new Map((recipe.loras || []).map((lora) => [lora.name, Number(lora.strength)]));
  $("#direction").value = recipe.sourceDirection ?? recipe.direction ?? "";
  state.aiPrompts.h3 = recipe.aiPrompt || "";
  state.aiPromptSources.h3 = recipe.sourceDirection ?? recipe.direction ?? "";
  $("#ai-generated-prompt").value = state.aiPrompts.h3;
  $("#ai-generated-panel").open = Boolean(state.aiPrompts.h3);
  if (state.h3Type === "frames") $("#frame-h3-model").value = recipe.baseModel || FRAME_H3_MODEL;
  else $("#h3-model").value = recipe.baseModel || LEGACY_H3_MODEL;
  $("#turbo-enabled").checked = recipe.turboEnabled ?? (state.h3Type === "frames" ? $("#frame-h3-model").value : $("#h3-model").value) !== EROSMAX_MODEL;
  $("#sparse-enabled").checked = recipe.sparseEnabled ?? recipe.vsaEnabled ?? ($("#h3-model").value === OMNI_MODEL);
  $("#sparse-method").value = ["sla", "vsa", "sol-attn"].includes(recipe.sparseMethod) ? recipe.sparseMethod : "vsa";
  $("#frame-turbo-strength").value = recipe.turboStrength ?? 0.75;
  $("#aspect-ratio").value = normalizeAspect(recipe.aspect || $("#aspect-ratio").value);
  $("#megapixels").value = recipe.megapixels ?? 0.5;
  $("#total-steps").value = recipe.steps ?? PRESET_STEPS[recipe.turbo] ?? 4;
  $("#h3-single-frame").checked = recipe.singleFrame === true;
  $("#h3-audio-lock").checked = recipe.audioLock === true;
  $("#duration").value = recipe.duration ?? 5;
  $("#seed").value = recipe.seed ?? -1;
  if (recipe.renderTarget) $("#render-target").value = recipe.renderTarget;
  $("#ai-enhance").checked = recipe.enhancePrompt !== false;
  $("#ai-model").value = recipe.aiModel || $("#ai-model").value;
  const turbo = studioRoot.querySelector(`input[name="turbo"][value="${recipe.turbo || 4}"]`);
  if (turbo) turbo.checked = true;
  const jobType = studioRoot.querySelector(`input[name="job-type"][value="${recipe.jobType || "auto"}"]`);
  if (jobType) jobType.checked = true;
  syncTurboStack();
  syncH3OutputMode();
  $("#ai-enhance").dispatchEvent(new Event("change"));
  renderReferences();
  renderFrameInputs();
  renderSelectedLoras();
  renderLoraList();
  setStatus("Setup restored", "Raise resolution or steps, then run H3 again.", null);
  addLog(`Restored H3 setup with seed ${recipe.seed ?? "random"}.`, "success");
  window.scrollTo({ top: 0, behavior: "smooth" });
  toast("Setup restored. Change megapixels or steps, then run H3.");
}

function requeueRecipe(recipe) {
  if (loadRecipe({ ...recipe, enhancePrompt: false }) === false) return;
  setTimeout(() => $("#studio-form").requestSubmit(), 0);
}

async function keepSetup(file, button) {
  try {
    const name = window.prompt("Name this preset", file.preset_name || file.recipe?.direction?.slice(0, 60) || "Pocket preset");
    if (name === null) return;
    await saveRecipe(file.prompt_id, { recipe: file.recipe, ...(file.filename ? { output: file } : {}), saved: true, preset_name: name, timings: file.timings || {} });
    file.saved = true;
    file.preset_name = name;
    button.textContent = "Rename preset";
    rememberSetup(file);
    renderGallery(state.galleryFiles);
    toast("Preset saved.");
  } catch (error) {
    toast(error.message);
  }
}

async function removeGalleryItem(file, presetOnly = false) {
  if (!window.confirm(presetOnly ? "Remove this saved preset? The job and output remain." : "Remove this entry from the gallery and saved presets? The output file stays on disk.")) return;
  try {
    await saveRecipe(file.prompt_id, { saved: false, ...(presetOnly ? {} : {hidden: true}) });
    try { localStorage.setItem(SAVED_SETUPS_KEY, JSON.stringify(savedSetups().filter(item => item.prompt_id !== file.prompt_id))); } catch { /* Server remains authoritative. */ }
    file.saved = false;
    if (!presetOnly) state.galleryFiles = state.galleryFiles.filter(item => item.prompt_id !== file.prompt_id);
    renderGallery(state.galleryFiles);
    toast(presetOnly ? "Preset removed." : "Entry removed. Output file kept.");
  } catch (error) { toast(error.message); }
}

function formatElapsed(value) {
  if (!Number.isFinite(value)) return "Not recorded";
  return value < 60 ? `${value.toFixed(1)}s` : `${Math.floor(value / 60)}m ${(value % 60).toFixed(0)}s`;
}

function viewGalleryOutput(file) {
  const media = document.createElement(/\.(mp4|webm|mov|mkv)$/i.test(file.filename) ? "video" : /\.(mp3|wav|flac|ogg|m4a)$/i.test(file.filename) ? "audio" : "img");
  media.src = mediaUrl(file);
  if (["VIDEO", "AUDIO"].includes(media.tagName)) { media.controls = true; media.playsInline = true; media.autoplay = true; }
  else media.alt = file.recipe?.direction || file.filename;
  $("#gallery-viewer-media").replaceChildren(media);
  $("#gallery-viewer-title").textContent = file.preset_name || file.filename;
  $("#gallery-viewer").showModal();
}

const gallerySelection = new Set();
let galleryShown = [], removingGallery = false;
function updateGallerySelection() {
  const button = $("#gallery-remove-selected");
  button.textContent = removingGallery ? "Removing…" : `Remove selected (${gallerySelection.size})`;
  button.disabled = removingGallery || !gallerySelection.size;
  $("#gallery-select-page").disabled = removingGallery || !galleryShown.length;
  $("#gallery-clear-selection").disabled = removingGallery || !gallerySelection.size;
}

async function removeSelectedGalleryItems() {
  if (removingGallery || !gallerySelection.size) return;
  const ids = [...gallerySelection];
  if (!window.confirm(`Remove ${ids.length} selected entries and saved presets? Media files stay on disk.`)) return;
  removingGallery = true;
  ++galleryLoad;
  renderGallery(state.galleryFiles);
  let failed = 0;
  for (const id of ids) {
    try {
      await saveRecipe(id, {saved: false, hidden: true});
      try { localStorage.setItem(SAVED_SETUPS_KEY, JSON.stringify(savedSetups().filter(item => item.prompt_id !== id))); } catch { /* Server remains authoritative. */ }
      gallerySelection.delete(id);
      state.galleryFiles = state.galleryFiles.filter(item => item.prompt_id !== id);
    } catch { failed++; }
  }
  removingGallery = false;
  renderGallery(state.galleryFiles);
  toast(failed ? `${ids.length - failed} removed; ${failed} failed. Failed entries remain selected for retry.` : `${ids.length} entries removed. Media files kept.`);
}

function outputSlate(file) {
  const slate = document.createElement("div");
  slate.className = "rendering-slate";
  slate.setAttribute("role", "status");
  const title = document.createElement("strong");
  title.textContent = file.status === "Running" ? "Rendering" : file.status || "Awaiting output";
  const note = document.createElement("span");
  const progress = Number(file.progress);
  note.textContent = file.status === "Running" && Number.isFinite(progress)
    ? `${Math.max(0, Math.min(100, Math.round(progress)))}%` : file.message || "Your output will appear here";
  slate.append(title, note);
  return slate;
}

function outputActions(file, media = null) {
  const actions = document.createElement("div");
  if (["Running", "Queued", "Cancelling"].includes(file.status)) return actions;
  actions.className = "gallery-card-actions";
  if (["h3", "qwen", "faces", "krea", "ltx"].includes(file.recipe?.mode)) {
    const reuse = document.createElement("button");
    reuse.type = "button";
    reuse.className = "reuse-setup";
    reuse.textContent = "Edit & rerun";
    reuse.addEventListener("click", () => { location.hash = "#generator"; loadRecipe(file.recipe); });
    const requeue = document.createElement("button");
    requeue.type = "button";
    requeue.textContent = "Requeue same";
    requeue.addEventListener("click", () => { location.hash = "#generator"; requeueRecipe(file.recipe); });
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = file.saved ? "Rename preset" : "Save preset";
    save.addEventListener("click", () => keepSetup(file, save));
    actions.append(reuse, requeue, save);
    if (file.saved) {
      const unsave = document.createElement("button"); unsave.type = "button"; unsave.textContent = "Remove preset";
      unsave.onclick = () => removeGalleryItem(file, true); actions.append(unsave);
    }
  }
  if (file.filename) {
    const view = document.createElement("button"); view.type = "button"; view.textContent = "View / play"; view.className = "reuse-setup";
    view.onclick = () => viewGalleryOutput(file); actions.prepend(view);
    if (media?.tagName === "IMG") { media.style.cursor = "pointer"; media.onclick = () => viewGalleryOutput(file); }
    const share = document.createElement("button");
    share.type = "button";
    share.textContent = isImageOutput(file) ? "Save image" : "Save video";
    share.addEventListener("click", () => shareMedia(file));
    actions.append(share);
    const download = document.createElement("a"); download.href = mediaUrl(file); download.download = file.filename; download.textContent = "Download"; actions.append(download);
  }
  return actions;
}

function renderGallery(files) {
  state.galleryFiles = files;
  const selectable = new Set(files.filter(file => !["Running", "Queued", "Cancelling"].includes(file.status)).map(file => file.prompt_id));
  for (const id of gallerySelection) if (!selectable.has(id)) gallerySelection.delete(id);
  const grid = $("#gallery-grid");
  grid.replaceChildren();
  const search = $("#gallery-search").value.trim().toLowerCase();
  const visible = files.filter(file => ($("#gallery-collection").value !== "saved" || file.saved) &&
    [file.preset_name, file.filename, file.prompt_id, file.recipe?.sourceDirection, file.recipe?.direction, file.recipe?.aiPrompt, file.recipe?.baseModel, file.recipe?.seed].join(" ").toLowerCase().includes(search));
  const sort = $("#gallery-sort").value;
  visible.sort((a,b) => sort === "slowest" ? (b.timings?.total_seconds ?? -1) - (a.timings?.total_seconds ?? -1) : (sort === "oldest" ? 1 : -1) * ((a.modified || 0) - (b.modified || 0)));
  const size = Number($("#gallery-size").value), pages = Math.max(1, Math.ceil(visible.length / size));
  state.galleryPage = Math.min(state.galleryPage, pages - 1);
  $("#gallery-page").textContent = `Page ${state.galleryPage + 1} of ${pages}`;
  $("#gallery-prev").disabled = state.galleryPage === 0;
  $("#gallery-next").disabled = state.galleryPage + 1 >= pages;
  grid.classList.toggle("compact", $("#gallery-layout").value === "compact");
  $("#gallery-status").textContent = `${visible.length} matching jobs · ${files.filter(file => file.saved).length} saved presets · ${files.length} loaded`;
  if (!visible.length) grid.textContent = "No matching jobs or presets.";
  const shown = visible.slice(state.galleryPage * size, (state.galleryPage + 1) * size);
  galleryShown = shown.filter(file => selectable.has(file.prompt_id));
  updateGallerySelection();
  shown.forEach((file) => {
    const card = document.createElement("article");
    card.className = "gallery-card";
    const preview = file.filename ? file : file.thumbnailFile || file;
    const isVideo = /\.(mp4|webm|mov|mkv)$/i.test(preview.filename || "") || preview.kind === "video";
    let media;
    if (!file.filename && file.status && file.status !== "Saved preset") {
      media = outputSlate(file);
    } else if (preview.filename) {
      if (/\.(mp3|wav|flac|ogg|m4a)$/i.test(preview.filename)) {
        media=document.createElement('audio');media.controls=true;media.preload='none';media.src=mediaUrl(preview);
      } else if (state.controllerMedia) {
        media = document.createElement("img");
        media.src = previewUrl(preview);
        media.loading = "lazy";
        media.decoding = "async";
        media.alt = isVideo ? "Video preview · tap View / play" : "Job thumbnail";
      } else {
        media = document.createElement(isVideo ? "video" : "img");
        media.src = mediaUrl(preview);
        if (isVideo) { media.preload = "none"; media.controls = true; media.playsInline = true; }
        else { media.loading = "lazy"; media.alt = "Job thumbnail"; }
      }
    } else {
      media = document.createElement("div");
      media.className = "gallery-placeholder";
      media.textContent = "H3";
    }
    const copy = document.createElement("div");
    copy.className = "gallery-card-copy";
    if (selectable.has(file.prompt_id)) {
      const selection = document.createElement("label"), check = document.createElement("input");
      selection.textContent = " Select entry";
      check.type = "checkbox"; check.style.width = "auto"; check.checked = gallerySelection.has(file.prompt_id); check.disabled = removingGallery;
      check.onchange = () => { if (check.checked) gallerySelection.add(file.prompt_id); else gallerySelection.delete(file.prompt_id); updateGallerySelection(); };
      selection.prepend(check); copy.append(selection);
    }
    const name = document.createElement("span");
    name.textContent = file.preset_name || `${file.status}${file.recipe ? ` · ${file.recipe.megapixels}MP · ${file.recipe.steps} steps` : ""}`;
    name.title = file.filename || file.prompt_id;
    const actions = outputActions(file, media);
    const original = file.recipe?.sourceDirection ?? file.recipe?.direction ?? "";
    const prompt = document.createElement("p"); prompt.className = "gallery-prompt"; prompt.textContent = original || "No prompt recorded";
    const aiPrompt = file.recipe?.aiPrompt && file.recipe.aiPrompt !== original ? document.createElement("details") : null;
    if (aiPrompt) {
      aiPrompt.className = "gallery-ai-prompt";
      const label = document.createElement("summary"), text = document.createElement("p");
      label.textContent = "AI enhanced prompt";
      text.textContent = file.recipe.aiPrompt;
      aiPrompt.append(label, text);
    }
    const metadata = document.createElement("div"); metadata.className = "gallery-metadata";
    metadata.textContent = `Total ${formatElapsed(file.timings?.total_seconds)} · Render ${formatElapsed(file.timings?.generation_seconds)} · Queue ${formatElapsed(file.timings?.queue_seconds)}`;
    const details = document.createElement("details"), summary = document.createElement("summary"), data = document.createElement("pre");
    summary.textContent = "Settings & job details";
    data.textContent = JSON.stringify({job: file.prompt_id, created: file.modified > 1000000000 ? new Date(file.modified * 1000).toLocaleString() : null, recipe: file.recipe, timings: file.timings}, null, 2);
    details.append(summary, data);
    if (!["Running", "Queued", "Cancelling"].includes(file.status)) {
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove entry"; remove.className = "danger"; remove.onclick = () => removeGalleryItem(file); actions.append(remove);
    }
    copy.append(name, prompt, ...(aiPrompt ? [aiPrompt] : []), metadata, details, actions);
    card.append(media, copy);
    grid.append(card);
  });
}

let galleryLoad = 0;
async function loadGallery() {
  if (removingGallery) return;
  const load = ++galleryLoad;
  $('#gallery-status').textContent = 'Loading library…';
  try {
    const [outputResponse, libraryResponse, statusResponse] = await Promise.all([
      fetch('/api/v1/outputs', {cache:'no-store'}), fetch('/api/v1/generator/library'), fetch('/api/v1/generator/status')]);
    if (!outputResponse.ok || !libraryResponse.ok || !statusResponse.ok) throw new Error('Could not load the library');
    const [outputs, library, status] = await Promise.all([outputResponse.json(),libraryResponse.json(),statusResponse.json()]);
    const hidden = new Set(status.hidden_ids || []);
    if (load === galleryLoad) renderGallery(recentJobs([...outputs,...library,...savedSetups()],{},{}).filter(file=>!hidden.has(file.prompt_id)));
  } catch(error) { if(load === galleryLoad) $('#gallery-status').textContent = error.message; }
}

window.addEventListener('tensor-master:job-submitted', () => {
  if (!studioRoot.hidden && !$("#gallery-panel").hidden) void loadGallery();
});
setInterval(() => {
  if (!document.hidden && !studioRoot.hidden && !$("#gallery-panel").hidden && !removingGallery) void loadGallery();
}, 5000);

async function showGallery() {
  studioRoot.classList.add("library-open");
  $("#studio-form").hidden = true;
  $("#result-panel").hidden = true;
  $("#run-log-panel").hidden = true;
  $("#sources-panel").hidden = true;
  $("#gallery-panel").hidden = false;
  studioRoot.querySelectorAll(".mode-switch button").forEach((button) => button.classList.toggle("active", button.id === "gallery-button"));
  await loadGallery();
}

function enhancementEnabled(mode = state.mode) {
  return mode === "h3" ? $("#ai-enhance").checked : mode === "qwen" ? $("#qwen-enhance").checked : false;
}

function enhancementModel(mode = state.mode) {
  return mode === "h3" ? $("#ai-model").value : $("#qwen-ai-model").value;
}

// Stands in for the AI prompt while a run's graph is snapshotted before enhancement finishes.
const PROMPT_SLOT = "__POCKET_AI_PROMPT_SLOT__";

function withPrompt(graph, text) {
  const escape = (value) => JSON.stringify(value).slice(1, -1);
  return JSON.parse(JSON.stringify(graph).split(escape(PROMPT_SLOT)).join(escape(text)));
}

// Captures everything the writer needs from the form now, so it can run in the background.
function prepareWriter(mode, references, seed) {
  if (mode === 'h3') return {mode, payload:{prompt:$('#direction').value.trim(),references,
    frame_mode:state.h3Type === 'frames', job_type:studioRoot.querySelector('input[name="job-type"]:checked').value,
    aspect_ratio:$('#aspect-ratio').value, megapixels:Number($('#megapixels').value),
    duration:Number($('#duration').value), model:enhancementModel(mode)}};
  return {
    mode,
    payload: {
      prompt: $("#direction").value.trim(),
      mode: references.length ? "image-edit" : "text-to-image",
      reference_count: references.length,
      aspect_ratio: $("#aspect-ratio").value,
      megapixels: Number($("#megapixels").value),
      model: enhancementModel(mode),
    },
  };
}

async function writeAiPrompt(writer) {
  const url = writer.mode === 'qwen' ? '/api/v1/qwen/enhance' : '/api/v1/generator/h3-enhance';
  const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(writer.payload)});
  const body = await response.json().catch(()=>({}));
  if (!response.ok || !body.prompt) throw new Error(body.detail || 'Prompt enhancement failed');
  return body.prompt.trim();
}

function showAiPrompt(mode, original, generated) {
  // Only fill the editor if the user has not moved on to a different prompt meanwhile.
  if (state.mode !== mode || $("#direction").value.trim() !== original) return;
  state.aiPrompts[mode] = generated;
  state.aiPromptSources[mode] = original;
  $("#ai-generated-prompt").value = generated;
  $("#ai-generated-panel").open = true;
  syncAiPromptPanel();
}

async function generateAiPrompt(references) {
  const mode = state.mode;
  const original = $("#direction").value.trim();
  if (!original) throw new Error("Enter your original prompt first.");
  const seedValue = Number($("#seed").value);
  const seed = Number.isSafeInteger(seedValue) && seedValue >= 0 ? seedValue : Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
  setStatus("Enhancing prompt", mode === "h3" ? "Running the H3 prompt writer" : "Writing a Qwen production prompt", 8);
  $("#ai-generated-status").textContent = "Generating from your original prompt…";
  addLog(`Generating a separate ${mode === "h3" ? "H3" : "Qwen"} AI prompt with ${(mode === "h3" ? $("#ai-model") : $("#qwen-ai-model")).selectedOptions[0].textContent}.`);
  const generated = await writeAiPrompt(prepareWriter(mode, references, seed));
  showAiPrompt(mode, original, generated);
  addLog("AI prompt generated separately. Original prompt preserved.", "success");
  return generated;
}

async function promptReferences() {
  return state.mode === "h3" && state.h3Type === "frames" ? uploadFrameReferences() : uploadReferences();
}

// Uploaded files become saved references so a quick resubmit does not upload them again.
function keepUploadedReferences(references, frameMode) {
  if (frameMode) {
    state.frameFiles = { first_frame: null, last_frame: null };
    state.savedFrameRefs = Object.fromEntries(references.map((ref) => [ref.kind, { ...ref }]));
    renderFrameInputs();
  } else {
    state.files = [];
    state.savedReferences = references.map((ref) => ({ ...ref }));
    renderReferences();
  }
}

async function run(event) {
  event.preventDefault();
  if (state.submitting) return;
  if (state.mode === "h3" && window.creativeMaster?.enabled()) return window.creativeMaster.generate();
  if (state.mode === "ltx") return runLtx();
  const frameMode = state.mode === "h3" && state.h3Type === "frames";
  const voiceClone = state.mode === "voice" && studioRoot.querySelector('input[name="voice-mode"]:checked').value === "clone";
  const referenceCount = frameMode
    ? ["first_frame", "last_frame"].filter((kind) => state.frameFiles[kind] || state.savedFrameRefs[kind]).length
    : state.files.length + state.savedReferences.length;
  if (!referenceCount && ["faces", "krea"].includes(state.mode)) return toast(state.mode === "faces" ? "Add one video and one face photo." : "Add an image to edit.");
  if (!referenceCount && voiceClone) return toast("Add one audio or video voice reference.");
  if (state.mode === "faces") {
    const kinds = [...state.savedReferences.map((ref) => ref.kind), ...state.files.map((file) => file.type.startsWith("video/") ? "video" : "image")];
    if (!kinds.includes("video") || !kinds.includes("image")) return toast("Add one video and one face photo.");
  }
  const direction = state.mode === "faces" ? "Face Refine" : $("#direction").value.trim();
  if (!direction && (enhancementEnabled() || !$("#ai-generated-prompt").value.trim())) return toast("Enter a user prompt to enhance, or turn enhancement off to reuse the saved AI prompt.");
  const jobMode = state.mode;
  const enhance = enhancementEnabled(jobMode);
  const promptOnly = enhance && $("#ai-prompt-only").checked;
  const runLabel = `Run ${++state.runCounter}`;
  setSubmitting(true);
  clearError();
  setStatus("Submitting", "Preparing your job for the worker queue", null);
  addLog(`${runLabel}: starting ${frameMode ? "H3 first/last-frame" : jobMode === "h3" ? "H3 reference video" : jobMode === "qwen" ? (referenceCount ? "Qwen Image 2.1 edit" : "Qwen Image 2.1 text-to-image") : jobMode === "krea" ? "Krea 2 Identity" : jobMode === "faces" ? "H3 Face Refine" : "VoxCPM2 voice"} with ${referenceCount} reference${referenceCount === 1 ? "" : "s"}.`);
  $("#result-panel").hidden = true;
  try {
    if (frameMode && enhance && !state.promptDirectorReady) {
      throw new Error("Prompt Director's FL2V preset is not loaded. Check the worker status in Tensor Master.");
    }
    if (jobMode === "qwen" && !state.qwenReady) {
      throw new Error("Qwen Image 2.1 nodes are not loaded. Check the worker status in Tensor Master.");
    }
    const references = frameMode ? await uploadFrameReferences() : await uploadReferences();
    if (["h3", "qwen", "faces", "krea", "ltx"].includes(jobMode)) keepUploadedReferences(references, frameMode);
    if (promptOnly) {
      await generateAiPrompt(references);
      setStatus("AI prompt ready", "Review or edit it, then turn off Prompt only when you want to render.", null);
      toast("AI prompt generated. No render was queued.");
      return;
    }
    // Snapshot the whole run synchronously; later form edits must not leak into it.
    const requestedSeed = Number($("#seed").value);
    const qwenRequestedSeed = Number($(jobMode === "faces" ? "#face-seed" : "#qwen-seed").value);
    const seed = jobMode === "krea" && Number.isSafeInteger(state.kreaSeed) ? state.kreaSeed : ["qwen", "faces"].includes(jobMode) && Number.isSafeInteger(qwenRequestedSeed) && qwenRequestedSeed >= 0
      ? qwenRequestedSeed
      : jobMode === "h3" && Number.isSafeInteger(requestedSeed) && requestedSeed >= 0
      ? requestedSeed
      : Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
    const existing = ["h3", "qwen"].includes(jobMode) ? $("#ai-generated-prompt").value.trim() : "";
    const writer = enhance ? prepareWriter(jobMode, references, seed) : null;
    state.runPrompt = writer ? PROMPT_SLOT : existing || direction;
    const graph = buildWorkflow(references, seed);
    state.runPrompt = null;
    const recipe = jobMode === "h3" ? captureH3Recipe(references, seed) : jobMode === "qwen" ? captureQwenRecipe(references, seed) : jobMode === "faces" ? captureFaceRecipe(references, seed) : jobMode === "krea" ? captureKreaRecipe(references, seed) : {mode:'voice',direction,references,voiceLora:$('#voice-lora').value,
      voiceDesign:$('#voice-design').value,voiceCfg:Number($('#voice-cfg').value),voiceSteps:Number($('#voice-steps').value),
      voiceMode:studioRoot.querySelector('input[name="voice-mode"]:checked').value,cleanVoice:$('#clean-voice').checked,denoiseBackend:$('#denoise-backend').value};
    const target = $("#render-target").value;
    const localQwenLoras = ["qwen", "krea"].includes(jobMode) && selectedLoraEntries(jobMode).length > 0;
    if (localQwenLoras && target === "tensor-master-cloud") {
      throw new Error("Selected LoRAs are installed only on this PC. Choose Any available worker, Tensor Master local 3090, or the local worker pool.");
    }
    const localFaceModel = jobMode === "faces" && $("#face-model").value === LEGACY_H3_MODEL;
    if (localFaceModel && target === "tensor-master-cloud") {
      throw new Error("The Hybrid checkpoint is only on this PC. Choose ErosMax or Ref2V for cloud workers.");
    }
    let finalPrompt = existing || direction;
    if (writer) {
      state.preparing += 1;
      setStatus("Enhancing prompt", `${runLabel} · preparing your submission`, 8);
      addLog(`${runLabel}: writing the AI prompt in the background.`);
      try { finalPrompt = await writeAiPrompt(writer); }
      finally { state.preparing -= 1; }
      showAiPrompt(jobMode, direction, finalPrompt);
      addLog(`${runLabel}: AI prompt ready. Original prompt preserved.`, "success");
    }
    const workflow = writer ? withPrompt(graph, finalPrompt) : graph;
    if (recipe) recipe.aiPrompt = enhance ? finalPrompt : existing;

    if (["h3", "qwen", "faces", "krea", "ltx", "voice"].includes(jobMode)) {
      const forceLocal = (localQwenLoras || localFaceModel) && target === "tensor-master-any";
      const cloud = target === "tensor-master-cloud";
      if (forceLocal) addLog("Q21 LoRA selected · routing to the local 3090 because these weights are not in the cloud manifest.");
      const response = await fetch("/api/v1/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: jobMode === "faces" ? `Pocket Face Refine · ${(references.find((ref) => ref.kind === "video")?.file || "").split("/").pop().slice(0, 72)}` : `${jobMode === "qwen" ? "Qwen Image 2.1" : jobMode === "krea" ? "Krea 2 Identity" : jobMode === "voice" ? "Myanmar Voice" : "H3"} · ${direction.slice(0, 72)}`,
          graph: workflow,
          recipe,
          policy: forceLocal ? "local" : target === "tensor-master-any" ? "any" : cloud ? "standard" : "local",
          min_vram_gb: 24,
          architecture: "any",
        }),
      });
      const dispatchText = await response.text();
      let result;
      try { result = JSON.parse(dispatchText); }
      catch {
        throw new Error(dispatchText.trim() === "Tensor Master is not configured yet."
          ? "Tensor Master is unavailable. Retry shortly."
          : `Tensor Master dispatch failed (HTTP ${response.status}). Check the service connection.`);
      }
      if (!response.ok) throw new Error(result.detail || result.message || "Tensor Master rejected the job");
      addLog(`${runLabel}: accepted by Tensor Master · ${result.job_id}`);
      confirmSubmission(result.job_id);
      const dispatch = { recipe, label: runLabel, startedAt: Date.now(), busy: false };
      dispatch.timer = setInterval(() => pollDispatchedJob(result.job_id), 3000);
      state.dispatches.set(result.job_id, dispatch);
      void pollDispatchedJob(result.job_id);
      return;
    }
  } catch (error) {
    state.runPrompt = null;
    setStatus("Could not start", `${runLabel}: ${error.message}`, null);
    showError(`${runLabel}: ${error.message}`);
    await updateQueue();
    toast(`${runLabel}: ${error.message}`);
  } finally {
    setSubmitting(false);
  }
}

async function initialize() {
  try {
    const response = await fetch('/api/v1/generator/catalog');
    if(response.status===401){$('#generator-sign-in').hidden=false;throw new Error('Sign in to Tensor Master to use Generate.');}
    if (!response.ok) throw new Error('Could not load generator catalog');
    const catalog = await response.json();
    $('#generator-sign-in').hidden = true;clearError();
    state.loras = catalog.loras;
    state.promptDirectorReady = true; state.qwenReady = true;
    $('#pocket-version').textContent = 'Tensor Master Generator';
    renderLoraList(); renderVoiceLoras();
    await updateQueue(); clearInterval(state.queueTimer); state.queueTimer = setInterval(updateQueue, 3000);
    addLog('Connected to Tensor Master. Jobs use the worker pool.', 'success');
  } catch(error) { setConnection(false); showError(error.message); }
}

function setMode(mode, h3Still = null) {
  const changed = mode !== state.mode;
  if (changed && ["h3", "qwen"].includes(state.mode)) {
    state.aiPrompts[state.mode] = $("#ai-generated-prompt").value.trim();
  }
  state.mode = mode;
  if (mode === "h3" && h3Still !== null) $("#h3-single-frame").checked = h3Still;
  if (changed) {
    state.files = [];
    state.savedReferences = [];
    state.frameFiles = { first_frame: null, last_frame: null };
    state.savedFrameRefs = {};
    state.selectedLoras.clear();
    renderReferences();
    renderFrameInputs();
    renderSelectedLoras();
    renderLoraList();
  }
  $("#studio-form").hidden = false;
  $("#studio-form").dataset.mode = mode;
  $("#run-log-panel").hidden = false;
  $("#sources-panel").hidden = true;
  $("#gallery-panel").hidden = true;
  studioRoot.classList.remove("library-open");
  const activeMode = mode === "h3" && $("#h3-single-frame").checked ? "h3-still" : mode;
  studioRoot.querySelectorAll(".mode-switch button").forEach((button) => button.classList.toggle("active", button.dataset.mode === activeMode));
  [[".h3-only", mode === "h3"], [".qwen-only", mode === "qwen"], [".faces-only", mode === "faces"], [".voice-only", mode === "voice"], [".ltx-only", mode === "ltx"], [".visual-only", !["voice", "faces", "ltx"].includes(mode)], [".dispatch-only", ["h3", "qwen", "faces", "krea", "ltx"].includes(mode)]].forEach(([selector, enabled]) => {
    studioRoot.querySelectorAll(selector).forEach((element) => {
      element.hidden = !enabled;
      element.querySelectorAll("input, select, textarea").forEach((control) => { control.disabled = !enabled; });
    });
  });
  $("#lora-panel").hidden = ["voice", "faces", "ltx"].includes(mode);
  $("#studio-subtitle").textContent = mode === "ltx" ? "LTX 2.5 · video upscale and HDR" : mode === "faces" ? "H3 Face Refine · sharpen small faces in a video" : mode === "h3" ? "MiniMax reference video" : mode === "qwen" ? "Qwen Image 2.1 generation and editing" : mode === "krea" ? "Krea 2 identity image edit" : "VoxCPM2 Myanmar speech";
  $("#direction").required = !["ltx", "faces"].includes(mode);
  $("#single-prompt").hidden = mode === "ltx";
  $(".hero-panel").hidden = mode === "ltx";
  $("#settings-title").closest("section").hidden = mode === "ltx";
  $("#prompt-title").textContent = mode === "voice" ? "Enter Myanmar speech" : "User prompt";
  $("#direction").placeholder = mode === "h3"
    ? "Replace the person in <Video 1> with the person from <Picture 1>. Keep the camera movement, lighting, clothes and original sound."
    : mode === "qwen" ? "A cinematic product photograph on a reflective studio surface, precise typography, soft directional lighting. Add reference images below only when editing." : mode === "krea" ? "Place the person from the second image into the scene. Preserve their exact facial identity and natural lighting." : "မြန်မာလို ပြောစေချင်တဲ့ စာသားကို ဒီမှာရေးပါ။";
  $("#reference-section-title").textContent = mode === "ltx" ? "Source video" : mode === "voice" ? "Voice reference" : mode === "faces" ? "Video and face" : mode === "h3" ? "References · optional" : "Add references";
  $("#reference-files").accept = mode === "ltx" ? "video/mp4,video/quicktime,video/webm,.mkv,.m4v" : mode === "faces" ? "image/*,video/*" : mode === "h3" ? "image/*,video/*,audio/*" : ["qwen", "krea"].includes(mode) ? "image/*" : "audio/*,video/*";
  $("#reference-files").multiple = !["voice", "ltx"].includes(mode);
  $("#reference-title").textContent = mode === "ltx" ? "Choose source video" : mode === "faces" ? "Choose the video and a face photo" : mode === "h3" ? "Choose photos, videos or audio" : mode === "qwen" ? "Add reference images · optional" : mode === "krea" ? "Choose image to edit" : "Choose audio or video";
  $("#reference-help").textContent = mode === "ltx" ? "One video · choose the clip start and length below" : mode === "faces" ? "One video plus one clear photo of the same person" : mode === "h3" ? "No reference creates video from your prompt · up to 9 photos, 3 videos and 3 audio files" : mode === "qwen" ? "No image creates from text · 1–10 images enables editing" : mode === "krea" ? "Optional second image: person or identity reference" : "Optional for TTS · required for voice cloning";
  $("#settings-title").textContent = mode === "voice" ? "Voice settings" : "Generation settings";
  $("#engine-note").textContent = mode === "ltx" ? "LTX 2.5 · full-frame conditioning · 8 steps · original audio kept" : mode === "faces"
    ? `Per-frame face tracking · 8 steps · ${$("#face-model").value === EROSMAX_MODEL ? "er_sde / beta57 · experimental VSA gate 0.75" : "Euler / simple"} · face-only paste-back · original audio kept`
    : mode === "h3"
    ? "Sampler: Euler · Scheduler: simple · VSA 0.75 · Turbo 1.0 · Comfy Kitchen"
    : mode === "qwen" ? "Qwen Image 2.1 INT8 · native 25 steps · CFG 1 · Euler / Simple" : mode === "krea" ? "Krea 2 Turbo · Identity Edit v1.2 · 10 steps · CFG 1" : "VoxCPM2 · 48 kHz · Myanmar · MP3 320 kbps";
  $("#run-button span").textContent = mode === "ltx" ? "Run LTX" : mode === "faces" ? "Refine faces" : mode === "h3" ? "Run H3" : mode === "qwen" ? "Create with Qwen 2.1" : mode === "krea" ? "Run Krea 2" : "Generate voice";
  syncPromptOnly();
  if (mode === "h3") syncTurboStack();
  if (mode === "qwen") {
    if (changed) { $("#megapixels").value = 1; $("#aspect-ratio").value = "1:1 (Square)"; }
    syncQwenTurbo();
    syncQwenEditCanvas();
  }
  renderSelectedLoras();
  renderLoraList();
  setH3Type(state.h3Type);
  syncVoiceMode();
  $("#result-panel").hidden = true;
  $("#ai-generated-prompt").value = state.aiPrompts[mode] || "";
  syncAiPromptPanel();
  clearError();
  window.creativeMaster?.sync();
}

function syncH3OutputMode() {
  const still = state.mode === "h3" && $("#h3-single-frame").checked;
  if (state.mode === "h3") studioRoot.querySelectorAll(".mode-switch button").forEach((button) => button.classList.toggle("active", button.dataset.mode === (still ? "h3-still" : "h3")));
  $("#h3-audio-lock").disabled = still;
  $("#h3-audio-lock-row").hidden = state.mode !== "h3" || state.h3Type === "frames" || still;
  $("#h3-duration-field").hidden = state.mode !== "h3" || still;
  $("#duration").disabled = state.mode !== "h3" || still;
  if (state.mode !== "h3") return;
  const frameMode = state.h3Type === "frames";
  $("#studio-subtitle").textContent = still
    ? (frameMode ? "MiniMax H3 text / keyframe still" : "MiniMax H3 reference still")
    : (frameMode ? "MiniMax first / last frame video" : "MiniMax reference video");
  $("#prompt-title").textContent = "User prompt";
  $("#engine-note").textContent = still
    ? "True single-frame H3 latent · Fizgig still decode · PNG · no audio file"
    : "Sampler: Euler · Scheduler: simple · VSA 0.75 · Turbo 1.0 · Comfy Kitchen";
  if (!$("#ai-prompt-only").checked) $("#run-button span").textContent = still ? "Create H3 still" : "Run H3";
  if (!still) syncTurboStack();
  syncPromptOnly();
}

function setH3Type(type) {
  const previousType = state.h3Type;
  state.h3Type = type === "frames" ? "frames" : "reference";
  if (previousType !== state.h3Type) markAiPromptStale();
  const frameMode = state.mode === "h3" && state.h3Type === "frames";
  $(".frame-mode-section").hidden = !frameMode;
  $(".reference-mode-section").hidden = frameMode;
  studioRoot.querySelectorAll(".reference-mode-only").forEach((element) => {
    if (state.mode === "h3") {
      element.hidden = frameMode;
      element.querySelectorAll("input, select, textarea").forEach((control) => { control.disabled = frameMode; });
    }
  });
  $(".frame-mode-only").hidden = !frameMode;
  $("#frame-h3-model").disabled = !frameMode;
  $("#frame-turbo-strength").disabled = !frameMode;
  $("#frame-strength-field").hidden = !frameMode;
  $(".turbo-stack-toggle").hidden = state.mode !== "h3";
  $("#lora-panel").hidden = ["voice", "faces", "ltx"].includes(state.mode);
  if (frameMode && state.selectedLoras.delete(VSA_LORA)) renderSelectedLoras();
  if (frameMode && previousType !== state.h3Type) {
    $("#turbo-enabled").checked = true;
    studioRoot.querySelector('input[name="turbo"][value="4"]').checked = true;
    $("#total-steps").value = $("#frame-h3-model").value === EROSMAX_MODEL ? 4 : 5;
    $("#frame-turbo-strength").value = 0.5;
  }
  if (!frameMode && previousType !== state.h3Type && state.mode === "h3") $("#turbo-enabled").checked = $("#h3-model").value !== EROSMAX_MODEL;
  const selected = studioRoot.querySelector(`input[name="h3-type"][value="${state.h3Type}"]`);
  if (selected) selected.checked = true;
  if (state.mode === "h3") {
    $("#studio-subtitle").textContent = frameMode ? "MiniMax first / last frame video" : "MiniMax reference video";
    $("#prompt-title").textContent = "User prompt";
    $("#direction").placeholder = frameMode
      ? "A slow camera push through a neon-lit street at night, reflections moving across wet pavement."
      : "Replace the person in <Video 1> with the person from <Picture 1>. Keep the camera movement, lighting, clothes and original sound.";
    syncPromptEnhancement();
    $("#turbo-stack-help").textContent = frameMode
      ? "Default: 768p non-SLA 4-step Turbo · strength 0.5 · 5 sampling steps · no VSA"
      : "LightX2V Ref2V · VSA optional and off by default";
    renderLoraList();
    syncTurboStack();
    syncH3OutputMode();
  }
}

function syncVoiceMode() {
  const clone = state.mode === "voice" && studioRoot.querySelector('input[name="voice-mode"]:checked').value === "clone";
  $("#clone-options").hidden = !clone;
  $("#clone-options").querySelectorAll("input, select").forEach((control) => { control.disabled = !clone; });
  $("#denoise-backend-field").hidden = !clone || !$("#clean-voice").checked;
  $("#denoise-backend").disabled = !clone || !$("#clean-voice").checked;
  if (state.mode === "voice") $("#reference-help").textContent = clone ? "One audio or video reference is required" : "Optional · add one only if you plan to switch to cloning";
}

function syncQwenTurbo() {
  const turbo = $("#qwen-turbo").checked;
  const steps = $("#qwen-steps");
  const wasTurbo = steps.dataset.turboActive === "true";
  if (turbo) {
    if (!wasTurbo) steps.dataset.nativeSteps = String(Number(steps.value) || 25);
    steps.value = 6;
  } else {
    if (wasTurbo) steps.value = String(Number(steps.dataset.nativeSteps) || 25);
    if (!Number.isFinite(Number(steps.value)) || Number(steps.value) < 1) steps.value = 25;
    steps.dataset.nativeSteps = steps.value;
  }
  steps.dataset.turboActive = String(turbo);
  steps.disabled = turbo;
  $("#qwen-cfg").disabled = turbo;
  $("#qwen-negative").disabled = turbo;
  $("#qwen-negative-field").hidden = turbo;
  $("#engine-note").textContent = turbo
    ? "Qwen Image 2.1 INT8 · Viggle Turbo v0.2.1 · 6 shifted Euler steps · unmerged LoRA 1.0 · CFG-free"
    : `Qwen Image 2.1 INT8 · native Euler/simple · ${$("#qwen-steps").value} steps · CFG ${$("#qwen-cfg").value}`;
}

function syncQwenEnhancement() {
  const enabled = $("#qwen-enhance").checked;
  $("#qwen-ai-model-field").hidden = !enabled;
  $("#qwen-enhance-description").textContent = enabled
    ? "Rewrite for Qwen Image 2.1 before generation"
    : "Use the saved AI prompt, or your user prompt if the AI field is empty";
  syncAiPromptPanel();
}

function syncAiPromptPanel() {
  scheduleDraftSave();
  const supported = ["h3", "qwen"].includes(state.mode);
  const original = $("#direction").value.trim();
  const generated = $("#ai-generated-prompt").value.trim();
  const visible = supported && (enhancementEnabled() || generated) && !(state.mode === "h3" && window.creativeMaster?.enabled());
  $("#ai-generated-panel").hidden = !visible;
  $("#ai-prompt-only").disabled = !supported || !enhancementEnabled();
  $("#direction").required = !["ltx", "faces"].includes(state.mode) && !(supported && !enhancementEnabled() && generated);
  if (!visible) return;
  $("#ai-generated-status").textContent = !generated
    ? "No AI prompt yet. Run, choose Prompt only, or click Generate again. Your original prompt is never replaced."
    : !enhancementEnabled()
      ? "AI enhancement is off · the text in this AI field will be rendered as written."
    : !original
      ? "Original user prompt was not saved for this older job. The final prompt is available here for reuse."
    : state.aiPromptSources[state.mode] !== original
      ? "User prompt changed · AI enhancement will generate a fresh prompt when you run."
      : "AI enhancement is on · a fresh AI prompt will be generated when you run. Turn it off to use this text as written.";
}
if (typeof window !== "undefined") window.syncAiPromptPanel = syncAiPromptPanel;

function markAiPromptStale() {
  if (["h3", "qwen"].includes(state.mode)) state.aiPromptSources[state.mode] = "";
  if (typeof syncAiPromptPanel === "function") syncAiPromptPanel();
}

function syncTurboStack() {
  $("#sparse-method").disabled = !$("#sparse-enabled").checked;
  const sparseLabel = $("#sparse-enabled").checked ? `Sparse ${$("#sparse-method").value.toUpperCase()}` : "Sparse attention off";
  const omni = state.h3Type !== 'frames' && $('#h3-model').value === OMNI_MODEL;
  if (omni) {
    $('#turbo-enabled').checked = false;
    $('#turbo-enabled').disabled = true;
    studioRoot.querySelector('input[name="turbo"][value="8"]').checked = true;
    studioRoot.querySelectorAll('input[name="turbo"]').forEach(input => {
      input.disabled = true;
      input.nextElementSibling.querySelector('small').textContent = 'Omni has built-in 8-step acceleration';
    });
    $('#total-steps').value = 8;
    $('#turbo-stack-help').textContent = 'FastH3 Omni · native 8-step model · add optional look LoRAs below';
    $('#engine-note').textContent = `Euler · simple · video/audio shifts 12/3 · guidance 1 · ${sparseLabel}`;
    return;
  }
  const eros = (state.h3Type === "frames" ? $("#frame-h3-model").value : $("#h3-model").value) === EROSMAX_MODEL;
  const singularity = state.h3Type !== 'frames' && $('#h3-model').value === SINGULARITY_MODEL;
  $("#turbo-enabled").disabled = eros || singularity;
  if (singularity) {
    $('#turbo-enabled').checked = true;
    if (studioRoot.querySelector('input[name="turbo"]:checked').value === '10') {
      studioRoot.querySelector('input[name="turbo"][value="4"]').checked = true;
      $('#total-steps').value = 4;
    }
  }
  if (eros) {
    $("#turbo-enabled").checked = false;
    if (studioRoot.querySelector('input[name="turbo"]:checked').value === "10") {
      studioRoot.querySelector('input[name="turbo"][value="4"]').checked = true;
      $("#total-steps").value = 4;
    }
    studioRoot.querySelectorAll('input[name="turbo"]').forEach((input) => {
      input.disabled = input.value === "10";
      input.nextElementSibling.querySelector("small").textContent = input.value === "10" ? "ACC requires a non-Eros model" : `${input.value}-step Eros · VSA gate 0.75`;
    });
    $("#turbo-stack-help").textContent = "Eros · 4 or 8 steps · experimental VSA gate · no external Turbo";
    $("#engine-note").textContent = "Sampler: er_sde · Scheduler: beta57 · experimental VSA gate 0.75";
    return;
  }
  studioRoot.querySelectorAll('input[name="turbo"]').forEach((input) => { input.disabled = singularity && input.value === '10'; });
  if (state.h3Type === "frames") {
    const enabled = $("#turbo-enabled").checked;
    studioRoot.querySelectorAll('input[name="turbo"]').forEach((input) => {
      input.nextElementSibling.querySelector("small").textContent = enabled ? `${input.value}-step FL2V Turbo 768p · strength ${$("#frame-turbo-strength").value} · no VSA` : "No external acceleration LoRA";
    });
    const model = $("#frame-h3-model").value === EROSMAX_MODEL ? "ErosMax (experimental)" : "FL2VA";
    $("#engine-note").textContent = `${model} · Euler · simple schedule · ${enabled ? `${FRAME_TURBO_LORAS[studioRoot.querySelector('input[name="turbo"]:checked').value]} · strength ${$("#frame-turbo-strength").value}` : "no external Turbo"} · no VSA`;
    return;
  }
  const enabled = $("#turbo-enabled").checked;
  const vsa = sparseLabel;
  studioRoot.querySelectorAll('input[name="turbo"]').forEach((input) => {
    input.nextElementSibling.querySelector("small").textContent = enabled
      ? `LightX2V Ref2V · ${input.value}-step LoRA · ${vsa}`
      : `${vsa} · no external Turbo LoRA`;
    if (singularity && input.value === '10') input.nextElementSibling.querySelector('small').textContent = 'Singularity uses 4-step or 8-step Ref2VA Turbo';
  });
  $('#turbo-stack-help').textContent = singularity
    ? `Singularity · matching 4/8-step Ref2VA LoRA required · ${vsa}`
    : `LightX2V Ref2V · matching 4/8-step LoRA · ${vsa}`;
  $("#engine-note").textContent = enabled
    ? `Sampler: Euler · Scheduler: simple · LightX2V Ref2V 1.0 · ${vsa} · Comfy Kitchen`
    : `Sampler: Euler · Scheduler: simple · ${vsa} · ${$("#h3-model").value === EROSMAX_MODEL ? "Built-in model turbo" : "No external Turbo LoRA"} · Comfy Kitchen`;
}

let sourcePage = 0, sourceLoad = 0;
async function showSources() {
  studioRoot.classList.add("library-open");
  for (const id of ["studio-form", "result-panel", "run-log-panel", "gallery-panel"]) $(`#${id}`).hidden = true;
  $("#sources-panel").hidden = false;
  studioRoot.querySelectorAll(".mode-switch button").forEach(button => button.classList.toggle("active", button.id === "sources-button"));
  await loadSources();
}

function useSource(file, frameKind) {
  if (state.mode === "h3" && state.h3Type === "frames" && !frameKind) return toast("Choose an image for a keyframe, or switch to Reference video to use this source.");
  if (frameKind) {
    state.savedFrameRefs[frameKind] = {kind: frameKind, file: file.file};
    state.frameFiles[frameKind] = null;
  } else {
    const accepted = state.mode === "h3" || (state.mode === "voice" && ["audio", "video"].includes(file.kind)) ||
      (["qwen", "krea"].includes(state.mode) && file.kind === "image") || (state.mode === "faces" && file.kind !== "audio") || (state.mode === "ltx" && file.kind === "video");
    if (!accepted) return toast("Choose a source supported by the current generator.");
    // Use the same capacity checks as newly selected files, including restored references.
    const fake = {name: file.filename, type: `${file.kind}/source`};
    if (!addFiles([fake], {kind: file.kind, file: file.file})) return;
  }
  const mode = state.mode, h3Type = state.h3Type;
  location.hash = "#generator";
  setMode(mode);
  if (mode === "h3") setH3Type(h3Type);
  renderReferences(); renderFrameInputs(); markAiPromptStale();
  toast("Source added. Other references are kept.");
}

async function loadSources() {
  const load = ++sourceLoad;
  $("#sources-status").textContent = "Loading sources…";
  try {
    const query = new URLSearchParams({kind: $("#sources-kind").value, search: $("#sources-search").value, page: sourcePage});
    const response = await fetch(`/api/v1/media/sources?${query}`);
    if (!response.ok) throw new Error(response.status === 401 ? "Sign in to Tensor Master to browse sources." : "Sources are available in Tensor Master.");
    const data = await response.json();
    if (load !== sourceLoad) return;
    state.controllerMedia = true;
    $("#sources-status").textContent = `${data.total} sources · page ${sourcePage + 1} of ${Math.max(1, Math.ceil(data.total / 24))}`;
    $("#sources-prev").disabled = sourcePage === 0;
    $("#sources-next").disabled = (sourcePage + 1) * 24 >= data.total;
    const grid = $("#sources-grid"); grid.replaceChildren();
    for (const file of data.items) {
      const card = document.createElement("article"); card.className = "gallery-card";
      if (file.kind !== "audio") {
        const image = document.createElement("img"); image.src = previewUrl(file); image.loading = "lazy"; image.alt = file.filename; card.append(image);
      }
      const copy = document.createElement("div"); copy.className = "gallery-card-copy";
      const name = document.createElement("p"); name.textContent = file.filename;
      const actions = document.createElement("div"); actions.className = "gallery-card-actions";
      if (state.mode === "h3" && state.h3Type === "frames" && file.kind === "image") {
        for (const [kind, label] of [["first_frame", "Use as first frame"], ["last_frame", "Use as last frame"]]) {
          const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.onclick = () => useSource(file, kind); actions.append(button);
        }
      } else {
        const button = document.createElement("button"); button.type = "button"; button.textContent = "Add to references"; button.onclick = () => useSource(file); actions.append(button);
      }
      const link = document.createElement("a"); link.href = mediaUrl(file); link.target = "_blank"; link.textContent = "Open original"; actions.append(link);
      copy.append(name, actions); card.append(copy); grid.append(card);
    }
  } catch (error) { if (load === sourceLoad) $("#sources-status").textContent = error.message; }
}

function poolTarget(target) {
  return ({local: "tensor-master", standard: "tensor-master-cloud", any: "tensor-master-any"})[target] || target || "tensor-master-any";
}

function poolPolicy(target) {
  return target === "tensor-master-cloud" ? "standard" : target === "tensor-master-any" ? "any" : "local";
}

function captureKreaRecipe(references, seed) {
  return {mode: "krea", references, seed, direction: $("#direction").value.trim(),
    sourceDirection: $("#direction").value.trim(), megapixels: Number($("#megapixels").value),
    aspect: $("#aspect-ratio").value, steps: 10, cfg: 1, renderTarget: $("#render-target").value,
    loras: selectedLoraEntries("krea").map(([name, strength]) => ({name, strength}))};
}

async function runLtx() {
  if (state.submitting) return;
  const sources = [...state.savedReferences, ...state.files];
  if (sources.length !== 1 || !(sources[0].kind === "video" || sources[0].type?.startsWith("video/") || /\.(mp4|mov|mkv|webm|m4v)$/i.test(sources[0].name || ""))) return toast("Choose one source video.");
  const policy = poolPolicy($("#render-target").value);
  const payload = {mode: $("#ltx-operation").value, prompt: $("#ltx-prompt").value,
    start: Number($("#ltx-start").value), duration: Number($("#ltx-duration").value),
    seed: Number($("#ltx-seed").value), policy};
  setSubmitting(true); clearError();
  setStatus("Submitting", "Preparing your job for the worker queue", null);
  try {
    const refs = await uploadReferences();
    payload.file = refs[0].file;
    setStatus("Preparing clip", "Preparing the source for the GPU worker pool", 5);
    const prepared = await fetch("/api/v1/ltx/prepare", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const setup = await prepared.json();
    if (!prepared.ok) throw new Error(setup.detail || "Could not prepare LTX clip");
    setup.recipe.renderTarget = poolTarget(policy);
    const response = await fetch("/api/v1/jobs", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: payload.mode === "hdr" ? "LTX SDR → HLG HDR" : "LTX 2× Upscale",
        graph: setup.graph, recipe: setup.recipe, policy, min_vram_gb: 24, architecture: "any"})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Tensor Master rejected the job");
    const dispatch = {recipe: setup.recipe, label: `LTX ${payload.mode}`, startedAt: Date.now(), busy: false};
    dispatch.timer = setInterval(() => pollDispatchedJob(result.job_id), 3000);
    state.dispatches.set(result.job_id, dispatch);
    addLog(`LTX accepted by Tensor Master · ${result.job_id}`, "success");
    confirmSubmission(result.job_id);
    void pollDispatchedJob(result.job_id);
  } catch (error) {
    setStatus("Could not start", error.message, null); showError(error.message);
  } finally { setSubmitting(false); }
}

$("#reference-files").addEventListener("change", (event) => addFiles(event.target.files));
initializeIsolationEvents();
$("#clear-prompt").addEventListener("click", () => {
  $("#direction").value = "";
  if (typeof syncAiPromptPanel === "function") syncAiPromptPanel();
  $("#direction").focus();
});
$("#direction").addEventListener("input", syncAiPromptPanel);
$("#ai-generated-prompt").addEventListener("input", () => {
  if (["h3", "qwen"].includes(state.mode)) state.aiPrompts[state.mode] = $("#ai-generated-prompt").value;
  syncAiPromptPanel();
});
$("#clear-ai-prompt").addEventListener("click", () => {
  $("#ai-generated-prompt").value = "";
  if (["h3", "qwen"].includes(state.mode)) {
    state.aiPrompts[state.mode] = "";
    state.aiPromptSources[state.mode] = "";
  }
  syncAiPromptPanel();
  $("#ai-generated-prompt").focus();
});
$("#regenerate-ai-prompt").addEventListener("click", async () => {
  if (!enhancementEnabled()) return toast("Turn on AI enhance prompt first.");
  if (!$("#direction").value.trim()) return toast("Enter your original prompt first.");
  setSubmitting(true); clearError();
  try {
    const references = await promptReferences();
    await generateAiPrompt(references);
    setStatus("AI prompt ready", "Review or edit the generated prompt. No render was queued.", null);
    toast("AI prompt generated. No render was queued.");
  } catch (error) {
    setStatus("Prompt enhancement failed", error.message, null);
    showError(error.message);
    toast(error.message);
  } finally {
    setSubmitting(false);
  }
});
function syncPromptOnly() {
  if (!$("#ai-prompt-only").checked || !["h3", "qwen"].includes(state.mode) || !enhancementEnabled()) return;
  $("#run-button span").textContent = state.mode === "h3" ? "Enhance H3 prompt only" : "Enhance Qwen prompt only";
}
$("#ai-prompt-only").addEventListener("change", () => {
  if (!$("#ai-prompt-only").checked) {
    if (state.mode === "h3") syncH3OutputMode();
    else $("#run-button span").textContent = "Create with Qwen 2.1";
  }
  else syncPromptOnly();
});
$("#h3-single-frame").addEventListener("change", () => {
  markAiPromptStale();
  syncH3OutputMode();
});
for (const id of ["duration", "aspect-ratio", "megapixels", "ai-model", "qwen-ai-model", "qwen-reference-resolution", "qwen-edit-canvas"]) {
  $(`#${id}`).addEventListener("change", markAiPromptStale);
}
studioRoot.querySelectorAll('input[name="job-type"]').forEach((input) => input.addEventListener("change", markAiPromptStale));
$("#toggle-loras").addEventListener("click", () => {
  const picker = $("#lora-picker");
  picker.hidden = !picker.hidden;
  $("#toggle-loras").textContent = picker.hidden ? "Add" : "Done";
  if (!picker.hidden) $("#lora-search").focus();
});
$("#lora-search").addEventListener("input", renderLoraList);
function syncPromptEnhancement() {
  const enabled = $("#ai-enhance").checked;
  const creative = state.mode === "h3" && $("#creative-enabled").checked;
  const frameMode = state.mode === "h3" && state.h3Type === "frames";
  $("#ai-model-field").hidden = !enabled;
  $("#ai-enhance-description").textContent = creative
    ? "Creative Engine handles enhancement for each video idea"
    : frameMode
    ? "Use H3 Prompt Director with OpenRouter"
    : "Use the H3 Reference Manager writer";
  $("#prompt-mode-note").textContent = creative
    ? "Creative Engine expands every video idea with the normal H3 writer using the Creative model above. Still images use the dedicated image writer."
    : !enabled
    ? "AI enhancement is off. H3 uses the AI prompt as written, or the user prompt if the AI field is empty."
    : frameMode
      ? "Prompt Director expands your scene using the first and/or last frame you supplied. Your OpenRouter key stays in Tensor Master."
      : "Uses your saved OpenRouter key and the Reference Manager's packaged H3 system prompt. The standalone word ‘flush’ is removed from the AI result.";
  syncAiPromptPanel();
  if (state.mode === "h3") syncH3OutputMode();
}
$("#ai-enhance").addEventListener("change", syncPromptEnhancement);
$("#qwen-enhance").addEventListener("change", () => {
  syncQwenEnhancement();
  $("#run-button span").textContent = "Create with Qwen 2.1";
  syncPromptOnly();
});
studioRoot.querySelectorAll('input[name="turbo"]').forEach((input) => {
  input.addEventListener("change", () => {
    $("#total-steps").value = state.h3Type === "frames" && $("#frame-h3-model").value !== EROSMAX_MODEL && input.value === "4" ? 5 : PRESET_STEPS[input.value];
    syncTurboStack();
  });
});
studioRoot.querySelectorAll('input[name="h3-type"]').forEach((input) => input.addEventListener("change", () => {
  if (input.checked) setH3Type(input.value);
}));
for (const [kind, prefix] of [["first_frame", "first"], ["last_frame", "last"]]) {
  $(`#${prefix}-frame`).addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file && !file.type.startsWith("image/")) return toast("Choose an image file for the frame.");
    state.frameFiles[kind] = file || null;
    delete state.savedFrameRefs[kind];
    markAiPromptStale();
    renderFrameInputs();
  });
  $(`#clear-${prefix}-frame`).addEventListener("click", () => {
    state.frameFiles[kind] = null;
    delete state.savedFrameRefs[kind];
    $(`#${prefix}-frame`).value = "";
    markAiPromptStale();
    renderFrameInputs();
  });
}
$("#turbo-enabled").addEventListener("change", syncTurboStack);
$("#sparse-enabled").addEventListener("change", syncTurboStack);
$("#sparse-method").addEventListener("change", syncTurboStack);
$("#qwen-turbo").addEventListener("change", syncQwenTurbo);
$("#qwen-steps").addEventListener("input", syncQwenTurbo);
$("#qwen-cfg").addEventListener("input", syncQwenTurbo);
$("#face-model").addEventListener("change", () => {
  $("#engine-note").textContent = `Per-frame face tracking · 8 steps · ${$("#face-model").value === EROSMAX_MODEL ? "er_sde / beta57 · experimental VSA gate 0.75" : "Euler / simple"} · face-only paste-back · original audio kept`;
});
$("#frame-turbo-strength").addEventListener("input", syncTurboStack);
$("#h3-model").addEventListener("change", () => {
  $("#turbo-enabled").checked = $("#h3-model").value !== EROSMAX_MODEL;
  sparseAttentionDefaults();
  syncTurboStack();
});
$("#frame-h3-model").addEventListener("change", () => {
  $("#turbo-enabled").checked = $("#frame-h3-model").value !== EROSMAX_MODEL;
  $("#total-steps").value = $("#frame-h3-model").value === EROSMAX_MODEL ? PRESET_STEPS[studioRoot.querySelector('input[name="turbo"]:checked').value] : 5;
  syncTurboStack();
});
studioRoot.querySelectorAll('input[name="voice-mode"]').forEach((input) => input.addEventListener("change", syncVoiceMode));
$("#clean-voice").addEventListener("change", syncVoiceMode);
$("#studio-form").addEventListener("submit", run);
studioRoot.querySelectorAll(".mode-switch button[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode === "h3-still" ? "h3" : button.dataset.mode, button.dataset.mode === "h3-still")));
$("#sources-button").onclick = showSources;
$("#refresh-sources").onclick = loadSources;
for (const id of ["sources-kind", "sources-search"]) $(`#${id}`).addEventListener("change", () => {sourcePage = 0; loadSources();});
$("#sources-prev").onclick = () => {sourcePage--; loadSources();};
$("#sources-next").onclick = () => {sourcePage++; loadSources();};
$("#gallery-button").addEventListener("click", showGallery);
$("#refresh-gallery").addEventListener("click", loadGallery);
$("#gallery-select-page").onclick = () => { for (const file of galleryShown) gallerySelection.add(file.prompt_id); renderGallery(state.galleryFiles); };
$("#gallery-clear-selection").onclick = () => { gallerySelection.clear(); renderGallery(state.galleryFiles); };
$("#gallery-remove-selected").onclick = removeSelectedGalleryItems;
for (const id of ["gallery-search", "gallery-collection", "gallery-sort", "gallery-size", "gallery-layout"]) {
  $(`#${id}`).addEventListener("input", () => { state.galleryPage = 0; renderGallery(state.galleryFiles); });
}
$("#gallery-prev").onclick = () => { state.galleryPage--; renderGallery(state.galleryFiles); };
$("#gallery-next").onclick = () => { state.galleryPage++; renderGallery(state.galleryFiles); };
$("#gallery-viewer").addEventListener("close", () => { $("#gallery-viewer-media video, #gallery-viewer-media audio")?.pause(); $("#gallery-viewer-media").replaceChildren(); });
$("#share-result").addEventListener("click", () => shareMedia(state.lastOutput));
$("#clear-log").addEventListener("click", () => {
  $("#run-log").textContent = "";
  $("#log-state").textContent = "Listening";
});
syncTurboStack();
syncPromptEnhancement();
syncPromptOnly();
async function restoreDispatchedJob(jobId = new URLSearchParams(location.search).get('edit_job')) {
  if(!jobId)return;
  const notice=document.createElement('p');notice.className='hint';notice.setAttribute('role','status');
  $('#studio-form').prepend(notice);
  try {
    if(!/^job-[a-f0-9]{12}$/.test(jobId))throw new Error('Invalid source job.');
    const response=await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    if(!response.ok)throw new Error('Could not load the source job from Tensor Master.');
    const source=await response.json();
    if(!source.edit?.recipe)throw new Error(source.edit?.warnings?.join(' ')||'This job has no recoverable Pocket settings.');
    loadRecipe(source.edit.recipe);
    notice.textContent=`Editing a copy of ${jobId}. Nothing has been queued. ${(source.edit.warnings||[]).join(' ')}`;
    addLog(notice.textContent,source.edit.warnings?.length?'error':'success');
  } catch(error) {
    notice.textContent=`Unable to restore: ${error.message}`;toast(notice.textContent);
  }
}
async function restoreInitialView() {
  await restoreDraft();
  if (new URLSearchParams(location.search).get("view") === "gallery") {
    await showGallery();
    return;
  }
  await restoreDispatchedJob();
}
$('#generator-sign-in').onclick = async () => {
  const response = await fetch('/api/v1/cloud/auth-status');
  const status = await response.json();
  const setup = !status.configured;
  $('#generator-login-form').dataset.setup = String(setup);
  $('#generator-confirm-field').hidden = !setup;
  $('#generator-confirm').required = setup;
  $('#generator-login-title').textContent = setup ? 'Create your Tensor Master password' : 'Sign in to Tensor Master';
  $('#generator-login-error').textContent = setup && !status.can_setup ? 'Set up your password on the host PC first.' : '';
  $('#generator-login-dialog').showModal();
};
$('#generator-login-close').onclick = () => $('#generator-login-dialog').close();
$('#generator-login-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const setup = $('#generator-login-form').dataset.setup === 'true';
    const password = $('#generator-password').value;
    if(setup && password !== $('#generator-confirm').value) throw new Error('Passwords do not match');
    const response = await fetch(`/api/v1/cloud/${setup ? 'password-setup' : 'login'}`, {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password,remember:$('#generator-remember').checked})});
    const body = await response.json();
    if(!response.ok)throw new Error(body.detail || 'Could not sign in');
    $('#generator-password').value='';$('#generator-confirm').value='';$('#generator-login-dialog').close();
    await initialize();await window.creativeMaster?.reconnect();
  } catch(error){$('#generator-login-error').textContent=error.message;}
};
window.tensorGenerator = {loadJob: restoreDispatchedJob, outputActions, outputSlate, showSources, showGenerate: () => setMode(state.mode)};
for (const event of ['input','change']) $('#studio-form').addEventListener(event,()=>{
  if (!draftReady) draftEarlyEdit=true;
  saveDraft();
});
window.addEventListener('pagehide',saveDraft);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')saveDraft();});
initialize().then(restoreInitialView);

/* Creative Engine owns concepts; existing H3 controls own the workflow. */
(() => {
  let batch = null, timer = null, busy = false, parent = null;
  let loadVersion = 0;
  const savedOutputs = new Set();
  const cards = new Map();
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  async function request(path = '', body) {
    const response = await fetch(`/api/v1/creative${path}`, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch {
      const known = {
        'Tensor Master is not configured yet.':'Tensor Master is unavailable. Retry shortly.',
        'Tensor Master is offline.':'Tensor Master is offline. Start Tensor Master, then click Refresh.',
      };
      throw new Error(known[text.trim()] || `Creative Engine request failed (HTTP ${response.status}). Check the service logs, then click Refresh.`);
    }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Creative request rejected. Check the brief and settings.');
    return data;
  }
  function message(text) { $('#creative-status').textContent = text; }
  function sync() {
    const enabled=state.mode==='h3' && $('#creative-enabled').checked;
    studioRoot.classList.toggle('creative-active',enabled);
    $('#creative-controls').hidden = !enabled;
    const noPrompt=enabled || ['faces','ltx'].includes(state.mode);
    $('#single-prompt').hidden=noPrompt;
    $('#direction').disabled=noPrompt;
    $('#direction').required=!noPrompt;
    $('#creative-brief').disabled=!enabled;
    $('#creative-brief').required=enabled;
    $('#ai-enhance').disabled=enabled;
    $('#ai-model').disabled=enabled;
    studioRoot.querySelectorAll('input[name="job-type"]').forEach(input=>{input.disabled=enabled || state.h3Type==='frames';});
    if(enabled && !$('#creative-brief').value)$('#creative-brief').value=$('#direction').value;
    syncPromptEnhancement();
    if (state.mode === 'h3') $('#run-button span').textContent = enabled ? ($('#creative-review').checked ? 'Generate ideas · review first' : 'Generate & queue') : 'Run H3';
  }
  function lock(value) {
    busy = value;
    setSubmitting(value);
    for (const id of ['creative-clear','creative-restore','creative-review','creative-queue','creative-refresh','creative-history']) $(`#${id}`).disabled = value;
  }
  async function history(selectId = null) {
    const batches = await request();
    const select = $('#creative-history');
    select.replaceChildren(new Option('Choose a saved batch', ''));
    for (const entry of batches) select.add(new Option(`${entry.created_at.slice(0,16).replace('T',' ')} · ${entry.brief.slice(0,70)}`,entry.id));
    select.value = selectId || batch?.id || '';
  }
  function draftRecipe(concept) {
    return normalizeRecipe(concept.recipe || {...batch.recipe, direction:batch.brief, sourceDirection:batch.brief,
      aiPrompt:concept.prompt, seed:concept.seed, enhancePrompt:false,
      renderTarget:batch.policy === 'any' ? 'tensor-master-any' : batch.policy === 'standard' ? 'tensor-master-cloud' : 'tensor-master'});
  }
  function restoreBrief(loaded = batch) {
    if (!loaded) return;
    // Restore the workflow snapshot first. loadRecipe intentionally exits Creative Engine,
    // so re-enable it only after all ordinary H3 controls and references are in place.
    if (loaded.recipe) loadRecipe(loaded.recipe);
    $('#creative-enabled').checked = true;
    $('#creative-brief').value = loaded.brief || '';
    $('#creative-count').value = String(loaded.count || 5);
    $('#creative-direction').value = loaded.direction || '';
    $('#creative-reference-notes').value = loaded.reference_notes || '';
    if (loaded.model) $('#creative-model').value = loaded.model;
    $('#render-target').value = loaded.policy === 'any' ? 'tensor-master-any' : loaded.policy === 'standard' ? 'tensor-master-cloud' : 'tensor-master';
    parent = loaded.id || loaded.parent || null;
    sync();
    $('#creative-restore').hidden = false;
    message('Original Creative brief and render setup restored. Edit them, then Run to generate a fresh batch. Generated concept prompts remain separate below.');
  }
  function render() {
    cards.clear();
    const container = $('#creative-cards'); container.replaceChildren();
    $('#creative-library').open = true;
    $('#creative-restore').hidden = false;
    for (const concept of batch.concepts) {
      const card = el('article',null,'creative-card');
      const label = el('label');
      const selected = el('input'); selected.type='checkbox'; selected.checked=!concept.job_id; selected.disabled=!!concept.job_id;
      const title = el('strong',`${concept.index+1}. ${concept.title}`);
      label.append(selected,title);
      if (concept.idea) {
        const idea = el('details',null,'creative-idea');
        idea.append(el('summary','Original idea'),el('p',concept.idea));
        card.append(idea);
      }
      const prompt = el('textarea'); prompt.value=concept.prompt; prompt.readOnly=!!concept.job_id;
      prompt.setAttribute('aria-label',`Final prompt for ${concept.title}`);
      const status = el('p',`Seed ${concept.seed}`,'hint');
      const output = el('div');
      const actions = el('div',null,'creative-actions');
      const edit = el('button','Edit & rerun','text-button'); edit.type='button';
      edit.onclick = () => loadRecipe({...draftRecipe(concept), direction:prompt.value});
      const requeue = el('button','Requeue same','text-button'); requeue.type='button';
      requeue.hidden=!concept.job_id; requeue.onclick=()=>requeueRecipe(draftRecipe(concept));
      const more = el('button','More like this','text-button'); more.type='button';
      more.onclick=()=>{
        loadRecipe({...draftRecipe(concept),direction:prompt.value});
        parent=`${batch.id}:${concept.index}`;
        $('#creative-enabled').checked=true;
        $('#creative-brief').value=prompt.value;
        $('#creative-direction').value='Develop distinct refinements of this concept. Preserve its core idea and supplied identities.';
        sync();
      };
      actions.append(edit,requeue,more);card.prepend(label);card.append(prompt,status,output,actions);container.append(card);
      cards.set(concept.index,{selected,prompt,status,output});
    }
    $('#creative-queue').hidden=batch.concepts.every(c=>c.job_id);
    updateProgress();
  }
  function outputFile(concept) {
    const task=concept.job?.tasks?.[0];
    if (!task?.artifact) return null;
    return {...task.artifact, ...(task.artifact.url ? {url:`/outputs/${encodeURIComponent(task.id)}/${encodeURIComponent(task.artifact.filename)}`} : {})};
  }
  function updateProgress() {
    for (const concept of batch.concepts) {
      const card=cards.get(concept.index); if(!card) continue;
      const task=concept.job?.tasks?.[0];
      card.status.textContent=`Seed ${concept.seed} · ${task?.status || (concept.job_id ? 'queued' : 'draft')}${task ? ` · ${task.progress}% · ${task.message || ''}` : ''}`;
      const file=outputFile(concept);
      if (file && !card.output.querySelector('video')) {
        const video=el('video'); video.controls=true; video.playsInline=true; video.preload='none'; video.src=mediaUrl(file);
        card.output.append(video);
        const link=el('a','Download video'); link.href=mediaUrl(file); link.download=file.filename; card.output.append(link);
      }
      if (task?.status==='completed' && file && task.prompt_id && !savedOutputs.has(task.id)) {
        savedOutputs.add(task.id);
        saveRecipe(task.prompt_id,{recipe:concept.recipe,output:file,timings:file.timings || {}})
          .catch(error=>{savedOutputs.delete(task.id);addLog(error.message,'error');});
      }
    }
  }
  function track() {
    clearTimeout(timer);
    if (!batch?.concepts.some(c=>c.job_id && !['completed','failed'].includes(c.job?.tasks?.[0]?.status))) return;
    const id=batch.id;
    timer=setTimeout(async()=>{
      try { const latest=await request(`/${id}`); if(batch?.id!==id)return;batch=latest;updateProgress(); }
      catch(error){message(`Status temporarily unavailable: ${error.message}`);}
      if(batch?.id===id)track();
    },4000);
  }
  async function queue() {
    if(busy || !batch) return;
    const concepts=batch.concepts.filter(c=>!c.job_id && cards.get(c.index).selected.checked)
      .map(c=>({index:c.index,title:c.title,prompt:cards.get(c.index).prompt.value.trim()}));
    if(!concepts.length)return message('Select at least one unqueued concept.');
    if(concepts.some(c=>!c.prompt))return message('Every selected concept needs a prompt.');
    lock(true);message('Queuing selected concepts…');
    try {
      batch=await request(`/${batch.id}/queue`,{concepts});render();track();
      confirmSubmission(batch.id);
      message(`Batch ${batch.id} · ${batch.concepts.filter(c=>c.job_id).length} jobs submitted. Follow progress here or in Tensor Master.`);
    } catch(error){message(`${error.message} Retry Queue selected safely; accepted concepts will not be duplicated.`);}
    finally{lock(false);}
  }
  async function generate() {
    if(busy)return;
    const brief=$('#creative-brief').value.trim();
    if(!brief)return toast('Enter your creative brief first.');
    const frames=state.h3Type==='frames';
    const review=$('#creative-review').checked;
    ++loadVersion;
    const snapshot={brief,count:Number($('#creative-count').value),direction:$('#creative-direction').value,
      reference_notes:$('#creative-reference-notes').value,model:$('#creative-model').value,
      policy:$('#render-target').value==='tensor-master-any'?'any':$('#render-target').value==='tensor-master-cloud'?'standard':'local',parent};
    lock(true);clearError();setStatus('Developing and enhancing','Creating ideas, then enhancing each with the H3 writer',null);$('#creative-library').open=true;message('Developing distinct ideas, then enhancing each prompt with your references… No renders queued yet.');
    try {
      // Freeze controls while uploads finish so the graph matches the submitted brief.
      const controls=[...$('#studio-form').querySelectorAll('input,select,textarea,button')];
      const previous=controls.map(c=>c.disabled); controls.forEach(c=>c.disabled=true);
      try {
        const references=frames?await uploadFrameReferences():await uploadReferences();
        snapshot.recipe={...captureH3Recipe(references,0),direction:brief,aiPrompt:'',enhancePrompt:false};
        snapshot.graph=buildH3Workflow(references,0,{});
      } finally {controls.forEach((c,i)=>c.disabled=previous[i]);}
      batch=await request('',snapshot);render();await history(batch.id);
      if(review){setStatus('Concepts ready',`${batch.concepts.length} ideas saved · review and queue selected`,null);toast(`${batch.concepts.length} concepts ready to review.`);}
      message(`Saved ${batch.concepts.length} concepts. ${review?'Review or edit the final prompts, then queue selected.':'Submitting variations…'}`);
      lock(false);
      if(!review)await queue();
    } catch(error){message(error.message);showError(error.message);}
    finally{lock(false);}
  }
  $('#creative-enabled').addEventListener('change',sync);
  $('#creative-review').addEventListener('change',sync);
  $('#creative-clear').onclick=()=>{
    if(busy)return;
    ++loadVersion;clearTimeout(timer);timer=null;batch=null;parent=null;cards.clear();
    $('#creative-cards').replaceChildren();$('#creative-history').value='';$('#creative-queue').hidden=true;$('#creative-restore').hidden=true;
    message('Loaded batch cleared. Saved batches and running jobs are unchanged. Your creative inputs are kept.');
  };
  $('#creative-restore').onclick=()=>restoreBrief();
  $('#creative-queue').onclick=queue;
  async function reconnect() {
    try {
      await history();
      $('#creative-enabled').disabled=false;
      $('#creative-availability').textContent='Turn one brief into distinct H3 variations';
    } catch(error) {
      $('#creative-availability').textContent=error.message;
      message(error.message);
    }
  }
  $('#creative-refresh').onclick=reconnect;
  $('#creative-history').onchange=async event=>{
    if(!event.target.value)return;
    const version=++loadVersion;
    try{clearTimeout(timer);const loaded=await request(`/${event.target.value}`);if(version!==loadVersion)return;batch=loaded;render();restoreBrief(loaded);track();}
    catch(error){if(version===loadVersion)message(error.message);}
  };
  window.creativeMaster={reconnect,enabled:()=>$('#creative-enabled').checked,generate,sync,
    single:()=>{$('#creative-enabled').checked=false;parent=null;sync();}};
  reconnect();
})();

})();
