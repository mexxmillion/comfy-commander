# Optional AI endpoints and model requirements

Rendering, queueing, model checks and deterministic workflow JSON validation do
not require an LLM. Optional prompt enhancement, Creative Engine ideas and
workflow-import review use the configuration in **Settings → Connections**.
AI assistance stays in prompt tools and workflow import. Workflow review cannot
install nodes, resolve unknown sources automatically or execute a workflow.

## Choose a provider

- **OpenRouter:** connect the key using its service card, select OpenRouter in
  the AI configuration, then validate/save. Existing generation model choices
  stay available.
- **OpenAI-compatible endpoint:** enter the API **base URL**, exact model ID and
  a separate key if needed. For example, a local server might expose
  `http://127.0.0.1:1234/v1`; OpenAI's base URL is `https://api.openai.com/v1`.
  Do not append `/chat/completions` to the base URL. The server must implement
  `/models` and `/chat/completions`. Responses-only APIs are not supported by
  this adapter.

For a local LLM server, load the desired model and configure its actual context
window before validating. Keyless localhost HTTP is supported. Remote endpoints
require HTTPS with a valid certificate. OpenRouter keys are never reused at a
custom endpoint; custom keys are bound to their saved endpoint URL. Keys stay
in the private credential directory and are never put in a recipe, graph or URL.

**Validate only** checks `/models` and the selected ID. **Validate & save** also
saves the configuration/key supplied by the human. Neither action requests a
completion or proves vision/JSON quality. Capability fields are declarations,
not facts inferred from a model name. Changing endpoints may require a new key.

## What the model must support

| Feature | Input/output requirements |
|---|---|
| Qwen prompt rewrite | Text; the writer receives the brief/reference count, not reference images |
| H3 text-only enhancement | Text |
| H3 enhancement with images | Vision accepting `image_url` data URLs |
| H3 enhancement with video | Native `video_url` input; this extension is not universally OpenAI-compatible |
| H3 enhancement with audio | `input_audio` with base64 WAV input |
| Creative Engine | Reliable JSON; vision when reference images are included. Video/audio notes are text at the idea stage; subsequent H3 enhancement needs the corresponding media support. |
| Optional workflow review | Text plus reliable JSON output; vision is not needed for workflow JSON |

Unsupported declared media is rejected before its request is sent. Keep AI
enhancement off to render without an LLM. Prompts/references are sent to the
chosen endpoint only when AI assistance is requested. A local endpoint keeps
that AI processing local; a hosted endpoint receives those inputs.

Motion-control video enhancement sends a compressed preview at the requested
render dimensions and duration, starting at the selected trim start. A shorter
selected interval is honored. The AI preview uses 8 fps to bound payload size;
rendering retains its normal frame rate and the source file is unchanged.
Use the video's **Process** button to scrub the source, set start/end seconds
or mark them at the playhead, then **Process & preview** and **Save reference**.
Changing the interval invalidates the old preview so it cannot be saved by mistake.

Commander accepts **32k context or larger**, and recommends **128k** for workflows
and media. Enter the context actually configured on your server. The output
limit defaults to 24,000 tokens; eight Creative variations may request 20,400.
Input plus output must fit in the context window. Commander estimates text/media
budget before sending; actual media tokenization depends on the provider.
These are application sizing choices, not universal model specifications.

Advanced options let you choose strict JSON schema, JSON object or prompt-only
JSON; `max_tokens` or `max_completion_tokens`; and whether temperature is
accepted. For a reasoning model that rejects temperature, turn it off. With
prompt-only JSON, the output must still parse and pass Commander's validation.
Do not select vision/audio/video capabilities unless both the model and endpoint
support that exact request format.

The [official Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
documents model-dependent text/image/audio inputs and parameter support. An API
being described as compatible does not guarantee every model accepts every field.

Drag the two handles on the video timeline to select the start and end. **Preview
selected range** plays only that interval immediately; **Process & preview**
creates the resized clip for review before **Save reference**. Exact time fields
and playhead buttons remain available for precise adjustments.
