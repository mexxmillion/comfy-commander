# Notices and third-party material

Comfy Commander Community

Copyright (C) 2026 mexxmillion

Original project code is available under `AGPL-3.0-or-later`. See `LICENSE`
and `LICENSING.md`.

This repository also refers to, downloads, patches, or retains material from
other projects. Those materials are not relicensed by the project's AGPL
notice. Their copyright notices, licenses, model cards, acceptable-use terms
and access requirements continue to apply.

In particular:

- ComfyUI and the custom-node sources pinned in
  `runtime/h3/sources.lock.json` retain their upstream licenses. The runtime
  builder preserves the upstream `LICENSE` and notice files it packages.
- Files under `runtime/h3/source-overlays/` and changes under
  `runtime/h3/patches/` interact with the named upstream projects and must be
  used together with the applicable upstream license notices.
- PromptDirector, ReferencePack and Pocket material with adjacent
  `*-LICENSE.txt` files retains the MIT notices in those files.
- Model weights are not licensed by this repository. Qwen, MiniMax H3, SAM3,
  Veda, LTX and any user-provided model or LoRA retain their respective model
  licenses and access terms. SAM3 may require the user to accept upstream terms
  before downloading its gated weights.
- Python dependencies listed in `pyproject.toml`, container base images and
  provider SDKs retain their respective licenses.

Before redistributing a built worker image or model bundle, review the exact
pinned revisions and include every notice required by those upstream works.

The reference-preprocessing subset under `runtime/h3/source-overlays/custom_nodes/ComfyUI-TensorMaster-Preprocessors` is MIT licensed, copyright 2026 Tensor Master contributors. Its LICENSE is retained alongside the source. Model weights and their licenses remain separate.
