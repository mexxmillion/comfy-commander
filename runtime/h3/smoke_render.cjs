// Submit the Pocket H3 FL2V graph to a local or private ComfyUI endpoint.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const endpoint = (process.env.COMFY_URL || "http://127.0.0.1:8189").replace(/\/$/, "");
const appPath = process.env.H3_APP_JS || "X:\\git\\ComfyUI\\custom_nodes\\h3_mobile\\web\\app.js";
const prompt = process.env.H3_SMOKE_PROMPT || "A glass orb slowly rotates on a dark tabletop, soft studio lighting, steady camera.";
const seed = Number(process.env.H3_SMOKE_SEED || "12345");
if (!Number.isSafeInteger(seed) || seed < 0) throw new Error("Invalid H3_SMOKE_SEED");
const source = fs.readFileSync(path.resolve(appPath), "utf8");
const cutoff = source.indexOf('$("#reference-files").addEventListener');
if (cutoff < 0) throw new Error("Pocket H3 graph builder was not found");

const values = {
  "#turbo-enabled": { checked: true },
  "#total-steps": { value: process.env.H3_SMOKE_STEPS || "4" },
  "#frame-turbo-strength": { value: process.env.H3_SMOKE_TURBO_STRENGTH || "0.75" },
  "#megapixels": { value: process.env.H3_SMOKE_MP || "0.2" },
  "#duration": { value: process.env.H3_SMOKE_DURATION || "5" },
  "#aspect-ratio": { value: "16:9 (Widescreen)" },
  "#ai-enhance": { checked: false },
  "#frame-h3-model": { value: process.env.H3_SMOKE_MODEL || "minimax_h3_fl2va_pruned_int8_convrot.safetensors" },
  "#render-target": { value: process.env.H3_SMOKE_LOCAL_CLIP === "1" ? "local" : "tensor-master-cloud" },
  "#h3-model": { value: process.env.H3_SMOKE_MODEL || "minimax_h3_ref2va_pruned_int8_convrot.safetensors" },
  "#direction": { value: prompt },
  "#ai-model": { value: "" },
};
const context = {
  crypto: { randomUUID: () => "tensor-h3-smoke" },
  document: {
    querySelector(selector) {
      if (selector === 'input[name="turbo"]:checked') return { value: "4" };
      if (selector === 'input[name="job-type"]:checked') return { value: "standard" };
      return values[selector] || null;
    },
  },
  console,
};
vm.createContext(context);
vm.runInContext(source.slice(0, cutoff), context);
vm.runInContext(`state.h3Type = ${JSON.stringify(process.env.H3_SMOKE_MODE || "frames")}`, context);
const references = JSON.parse(process.env.H3_SMOKE_REFS || "[]");
const graph = vm.runInContext(`buildH3Workflow(${JSON.stringify(references)}, ${seed})`, context);
graph["155"].inputs.filename_prefix = "H3/tensor_h3_smoke";
if (process.env.H3_GRAPH_ONLY === "1") {
  console.log(JSON.stringify(graph));
  process.exit(0);
}

async function request(resource, init) {
  const response = await fetch(`${endpoint}${resource}`, init);
  const body = await response.text();
  if (!response.ok) throw new Error(`${resource}: ${response.status} ${body}`);
  return JSON.parse(body);
}

async function main() {
  const start = Date.now();
  const submission = await request("/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: graph, client_id: "tensor-h3-smoke" }),
  });
  const acceptedAt = Date.now();
  if (!submission.prompt_id) throw new Error(`No prompt ID: ${JSON.stringify(submission)}`);
  console.log(`Submitted ${submission.prompt_id} to ${endpoint}`);
  const deadline = Date.now() + 45 * 60_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5000));
    const history = await request(`/history/${submission.prompt_id}`);
    const result = history[submission.prompt_id];
    if (!result) continue;
    const seconds = ((Date.now() - start) / 1000).toFixed(1);
    console.log(`Finished after ${seconds}s: ${JSON.stringify(result.status)}`);
    const events = result.status?.messages || [];
    const executionStart = events.find(([name]) => name === "execution_start")?.[1]?.timestamp;
    const executionSuccess = events.find(([name]) => name === "execution_success")?.[1]?.timestamp;
    console.log(`Timing: ${JSON.stringify({
      submit_to_accept_s: Number(((acceptedAt - start) / 1000).toFixed(1)),
      accept_to_result_s: Number(((Date.now() - acceptedAt) / 1000).toFixed(1)),
      comfy_execution_s: executionStart && executionSuccess
        ? Number(((executionSuccess - executionStart) / 1000).toFixed(1)) : null,
    })}`);
    const videos = result.outputs?.["155"]?.gifs || [];
    for (const video of videos) console.log(`Output: ${video.subfolder}/${video.filename}`);
    if (result.status?.status_str !== "success") process.exitCode = 1;
    return;
  }
  throw new Error(`Timed out waiting for ${submission.prompt_id}; inspect the ComfyUI queue`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
