# Tensor Master Community agent guidance

## Scope

This repository is the public Tensor Master Community controller. Keep changes
inside the public Qwen Image 2.1, MiniMax H3 Ref2VA, Singularity, FastH3 and SAM3
character-replacement scope. Never add production identities, private model
repositories, personal LoRAs or machine-specific credentials.

## Safety boundaries

- Treat rendering and GPU rental as external actions. Do not start either for a
  documentation, architecture, review or setup request.
- Never reveal or place API keys in prompts, graphs, recipes, logs or command
  arguments. Use the setup page and configured credential files.
- Use only controller APIs and allowlisted graphs. Do not expose ComfyUI directly
  or weaken a node allowlist to make a workflow pass.
- Submit once and retain the job ID. If a request times out, inspect state before
  retrying so a render cannot be duplicated.
- Keep provider price, fleet and lifetime limits intact. A human must authorize
  paid capacity.

## Character replacement

Use the guided SAM3 preparation endpoint. Review mask ownership and the target-
only inverted video before H3. The default accepted route uses that video as the
H3 Ref2VA reference and no `MiniMaxH3AddGuide` image nodes. Preserve continuous
source audio, exclusive frame intervals and rejected-run evidence. Rendered does
not mean QC-accepted.

## Verification

Run focused tests for changed code, validate `runtime/h3/models.lock.json`, and
scan the staged diff for secrets and private paths before proposing publication.
