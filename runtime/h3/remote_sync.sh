#!/usr/bin/env bash
set -euo pipefail
if [[ -s /workspace/hf_read.txt ]]; then
  HF_TOKEN="$(< /workspace/hf_read.txt)"
  export HF_TOKEN
else
  unset HF_TOKEN
fi
/opt/h3-venv/bin/python /opt/h3-runtime/sync_models.py \
  --profile "${H3_PROFILE:-ref2v-int8}" --models-dir /opt/ComfyUI/models
unset HF_TOKEN
echo MODELS_READY
