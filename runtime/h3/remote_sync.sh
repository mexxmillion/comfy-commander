#!/usr/bin/env bash
set -euo pipefail
HF_TOKEN="$(< /workspace/hf_read.txt)"
export HF_TOKEN
/opt/h3-venv/bin/python /opt/h3-runtime/sync_models.py \
  --profile "${H3_PROFILE:-ref2v-int8}" --models-dir /opt/ComfyUI/models
unset HF_TOKEN
echo MODELS_READY
