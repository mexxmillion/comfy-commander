#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${HF_TOKEN_FILE:-}" ]]; then
  HF_TOKEN="$(< "$HF_TOKEN_FILE")"
  export HF_TOKEN
fi

sync_args=(--profile "${H3_PROFILE:-ref2v-int8}" --models-dir /opt/ComfyUI/models)
if [[ "${H3_EXTRAS:-0}" == "1" ]]; then
  sync_args+=(--extras)
fi
python /opt/h3-runtime/sync_models.py "${sync_args[@]}"

cd /opt/ComfyUI
exec python main.py \
  --listen "${COMFY_LISTEN:-127.0.0.1}" \
  --port 8188 \
  --vram-headroom "${H3_VRAM_HEADROOM_GB:-2}" \
  --use-ck-attention \
  --fast fp16_accumulation \
  --disable-all-custom-nodes \
  --whitelist-custom-nodes \
  h3_beta57.py ComfyUI-Ref2VA-VSA Veda-on-ComfyUI h3_mobile viggle_turbo.py \
  comfyui-kjnodes \
  comfyui-minimaxrefpack \
  ComfyUI-PromptDirector \
  ComfyUI-H3-NativeAudioLock \
  comfyui-fizgig-h3-still \
  comfyui-videohelpersuite \
  comfyui-unload-model \
  comfyui-h3-facerefine \
  ComfyUI-LTXVideo comfyui-frame-interpolation comfyui-krea2edit ComfyUI-TensorMaster-Preprocessors
