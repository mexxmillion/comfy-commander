const fs = require("node:fs");
const vm = require("node:vm");

const appPath = process.env.POCKET_APP_JS || "X:\\git\\ComfyUI\\custom_nodes\\h3_mobile\\web\\app.js";
const source = fs.readFileSync(appPath, "utf8");
const script = source.slice(0, source.indexOf('$("#reference-files").addEventListener'));
const values = {
  "#qwen-turbo": { checked: process.env.QWEN_TURBO === "1" },
  "#qwen-negative": { value: "" },
  "#qwen-reference-resolution": { value: "1024" },
  "#qwen-cache": { value: "default" },
  "#qwen-steps": { value: "25" },
  "#qwen-cfg": { value: "1" },
  "#megapixels": { value: process.env.QWEN_MP || "0.25" },
  "#aspect-ratio": { value: "1:1 (Square)" },
  "#direction": { value: process.env.QWEN_PROMPT || "A small translucent blue glass bird on a black studio pedestal, clean product photograph, soft rim light, no text" },
};
const context = {
  crypto: { randomUUID: () => "tensor-qwen21-smoke" },
  document: { querySelector: (selector) => values[selector] },
  console,
};
vm.createContext(context);
vm.runInContext(script, context);
const smokeLoras = process.env.QWEN_LORA
  ? [[process.env.QWEN_LORA, Number(process.env.QWEN_LORA_STRENGTH || "0.75")]] : [];
vm.runInContext(`state.selectedLoras = new Map(${JSON.stringify(smokeLoras)})`, context);
const references = (process.env.QWEN_REFERENCES || "").split(",").filter(Boolean).map((file) => ({ kind: "image", file }));
const graph = vm.runInContext(`buildQwenWorkflow(${JSON.stringify(references)}, 246813579)`, context);

async function main() {
  const base = process.env.COMFY_URL || "http://127.0.0.1:8188";
  if (process.env.TENSOR_DISPATCH === "1") {
    const response = await fetch(`${base}/h3-mobile/dispatch`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      name: "Qwen Image 2.1 dispatch smoke", graph, policy: "local", min_vram_gb: 24, architecture: "any",
      recipe: { mode: "qwen", direction: values["#direction"].value, references, turbo: values["#qwen-turbo"].checked, steps: values["#qwen-turbo"].checked ? 6 : 25, cfg: 1, seed: 246813579, megapixels: Number(values["#megapixels"].value), aspect: values["#aspect-ratio"].value, loras: smokeLoras.map(([name, strength]) => ({name, strength})) },
    }) });
    const accepted = await response.json();
    if (!response.ok || !accepted.job_id) throw new Error(JSON.stringify(accepted));
    console.log(JSON.stringify({ event: "tensor_accepted", job_id: accepted.job_id }));
    const started = Date.now();
    while (Date.now() - started < 30 * 60 * 1000) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      const status = await (await fetch(`${base}/h3-mobile/dispatch/${accepted.job_id}`)).json();
      const task = status.tasks?.[0];
      if (task?.status === "failed") throw new Error(task.message);
      if (task?.status === "completed") {
        console.log(JSON.stringify({ event: "tensor_complete", job_id: accepted.job_id, seconds: (Date.now() - started) / 1000, worker_id: task.worker_id, artifact: task.artifact }));
        return;
      }
    }
    throw new Error("Tensor dispatch smoke test timed out");
  }
  const response = await fetch(`${base}/prompt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: graph, client_id: "tensor-qwen21-smoke" }) });
  const accepted = await response.json();
  if (!response.ok || accepted.error || !accepted.prompt_id) throw new Error(JSON.stringify(accepted));
  console.log(JSON.stringify({ event: "accepted", prompt_id: accepted.prompt_id, nodes: Object.keys(graph).length }));
  const started = Date.now();
  while (Date.now() - started < 30 * 60 * 1000) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const history = await (await fetch(`${base}/history/${accepted.prompt_id}`)).json();
    const entry = history[accepted.prompt_id];
    if (!entry) continue;
    const error = entry.status?.messages?.find((message) => message[0] === "execution_error");
    if (error) throw new Error(JSON.stringify(error[1]));
    for (const output of Object.values(entry.outputs || {})) {
      const image = output.images?.[0];
      if (image) {
        console.log(JSON.stringify({ event: "complete", prompt_id: accepted.prompt_id, seconds: (Date.now() - started) / 1000, image }));
        return;
      }
    }
    throw new Error("Workflow completed without a saved image");
  }
  throw new Error("Qwen smoke test timed out");
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
