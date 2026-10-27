#!/usr/bin/env bash
set -euo pipefail

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  ffmpeg git libgl1 libglib2.0-0 python3-venv
python -m venv --system-site-packages /opt/h3-venv
mkdir -p /workspace/h3-cloud-unpacked /opt/h3-runtime
tar -xzf /workspace/h3-cloud-runtime.tar.gz -C /workspace/h3-cloud-unpacked
cp -a /workspace/h3-cloud-unpacked/h3-runtime/. /opt/h3-runtime/
COMFYUI_REF=$(/opt/h3-venv/bin/python -c 'import json; print(json.load(open("/opt/h3-runtime/local-runtime.json"))["comfyui_revision"])')
git clone --filter=blob:none https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI
git -C /opt/ComfyUI checkout "$COMFYUI_REF"
cp -a /workspace/h3-cloud-unpacked/comfy-source/. /opt/ComfyUI/
cp -a /workspace/h3-cloud-unpacked/custom_nodes/. /opt/ComfyUI/custom_nodes/
cp -a /workspace/h3-cloud-unpacked/h3-runtime/. /opt/h3-runtime/
cp -a /workspace/h3-cloud-unpacked/backend /opt/h3-runtime/backend
cd /opt/ComfyUI
# Cloud workers only expose ComfyUI's API. The workflow-template package is a
# desktop gallery and currently pulls hundreds of megabytes of sample media;
# installing it adds several minutes on otherwise healthy rental hosts without
# affecting graph execution.
grep -v '^comfyui-workflow-templates==' requirements.txt > /tmp/h3-comfy-requirements.txt
/opt/h3-venv/bin/python -m pip install --no-cache-dir -c /opt/h3-runtime/local-runtime-constraints.txt -r /opt/h3-runtime/local-runtime-constraints.txt -r /tmp/h3-comfy-requirements.txt
/opt/h3-venv/bin/python -m pip install --no-cache-dir \
  -c /opt/h3-runtime/local-runtime-constraints.txt \
  -r custom_nodes/Veda-on-ComfyUI/requirements.txt \
  -r custom_nodes/comfyui-kjnodes/requirements.txt \
  -r custom_nodes/comfyui-minimaxrefpack/requirements.txt \
  -r custom_nodes/comfyui-videohelpersuite/requirements.txt \
  -r custom_nodes/ComfyUI-TensorMaster-Preprocessors/requirements.txt \
  ultralytics scipy comfy-kitchen==0.2.37 \
  huggingface_hub accelerate httpx websockets pydantic-settings
if [[ "${H3_PROFILE:-}" == ltx-* ]]; then
  # ComfyUI-LTXVideo HDR nodes: colour-science for the ACES/BT.2020 transforms, OpenImageIO for its HDR I/O.
  # Constrained to the captured local runtime so torch/transformers stay at the verified versions.
  /opt/h3-venv/bin/python -m pip install --no-cache-dir -c /opt/h3-runtime/local-runtime-constraints.txt \
    colour-science==0.4.6 openimageio==3.1.18.1 diffusers==0.36.0 einops==0.8.2 kornia==0.8.2 timm==1.0.26 \
    opencv-contrib-python-headless==4.11.0.86 cupy-cuda13x
  /opt/h3-venv/bin/python -c 'import colour, OpenImageIO; import cv2, cupy; print("LTX_HDR_DEPS_OK")'
fi
/opt/h3-venv/bin/python -c 'import importlib.metadata as m, json; expected=json.load(open("/opt/h3-runtime/local-runtime.json"))["packages"]; mismatch={n:(v,m.version(n)) for n,v in expected.items() if m.version(n)!=v}; assert not mismatch, mismatch; from transformers import CLIPTokenizer; print("LOCAL_RUNTIME_MATCH_OK")'
echo RUNTIME_READY
