#!/usr/bin/env bash
set -euo pipefail
umask 077
export TENSOR_MASTER_TOKEN_FILE=/workspace/tensor-master-token
export TENSOR_MASTER_BRIDGE_TOKEN_FILE=/workspace/tensor-pocket-token
if [[ -s /workspace/openrouter-key ]]; then
  OPENROUTER_API_KEY="$(< /workspace/openrouter-key)"
  export OPENROUTER_API_KEY
fi
cd /opt/ComfyUI
if ! pgrep -f '[p]ython.*main.py.*--port 8188' > /dev/null; then
nohup /opt/h3-venv/bin/python main.py \
  --listen 127.0.0.1 --port 8188 \
  --vram-headroom 2 --use-ck-attention --fast fp16_accumulation \
  --disable-all-custom-nodes --whitelist-custom-nodes \
  h3_beta57.py ComfyUI-Ref2VA-VSA Veda-on-ComfyUI viggle_turbo.py comfyui-kjnodes comfyui-minimaxrefpack \
  ComfyUI-PromptDirector ComfyUI-H3-NativeAudioLock comfyui-fizgig-h3-still comfyui-videohelpersuite comfyui-unload-model comfyui-h3-facerefine \
  ComfyUI-LTXVideo comfyui-frame-interpolation comfyui-krea2edit ComfyUI-TensorMaster-Preprocessors \
  > /workspace/h3-comfy.log 2>&1 < /dev/null &
echo COMFY_STARTED
else
echo COMFY_ALREADY_RUNNING
fi
unset OPENROUTER_API_KEY
cd /opt/h3-runtime
if ! pgrep -f '[p]ython.*-m backend.worker' > /dev/null; then
nohup env PYTHONPATH=/opt/h3-runtime TOKEN_FILE=/workspace/tensor-master-token \
  TENSOR_MASTER_URL=http://127.0.0.1:8000 COMFYUI_URL=http://127.0.0.1:8188 \
  COMFY_INPUT_DIR=/opt/ComfyUI/input COMFY_OUTPUT_DIR=/opt/ComfyUI/output \
  TENSOR_WORKER_STATE_DIR=/workspace/tensor-worker-state \
  TENSOR_WORKER_ID="${TENSOR_WORKER_ID:?}" \
  TENSOR_WORKER_PROVIDER="${TENSOR_WORKER_PROVIDER:-Vast.ai}" TENSOR_WORKER_RUNTIME="pocket-h3-cloud-${H3_PROFILE:?}" \
  TENSOR_WORKER_NAME="${TENSOR_WORKER_PROVIDER:-Vast.ai} Pocket worker" \
  TENSOR_WORKER_PRICE_PER_HOUR="${TENSOR_WORKER_PRICE_PER_HOUR:?}" \
  /opt/h3-venv/bin/python -m backend.worker \
  > /workspace/h3-worker.log 2>&1 < /dev/null &
echo WORKER_STARTED
else
echo WORKER_ALREADY_RUNNING
fi
