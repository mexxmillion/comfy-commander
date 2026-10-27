# syntax=docker/dockerfile:1.7
FROM pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime@sha256:1f57418aedd9a4d0d3a59646619e1d4f82cacc33817247cead4f749e1f452d4b

ARG COMFYUI_REF=36c0b0a687e5e6d7b55e3e61ab24262ffc0f2508

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git libgl1 libglib2.0-0 python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv --system-site-packages /opt/h3-venv
ENV PATH="/opt/h3-venv/bin:${PATH}"

RUN git clone --filter=blob:none https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout "$COMFYUI_REF"

WORKDIR /opt/ComfyUI
COPY --from=nodes /custom_nodes/ /opt/ComfyUI/custom_nodes/
COPY --from=nodes /runtime-snapshot/ /opt/h3-runtime/
COPY --from=nodes /comfy-source/ /opt/ComfyUI/
COPY models.lock.json sync_models.py start.sh /opt/h3-runtime/

RUN python -m pip install --no-cache-dir -c /opt/h3-runtime/local-runtime-constraints.txt -r /opt/h3-runtime/local-runtime-constraints.txt -r requirements.txt \
    && python -m pip install --no-cache-dir \
       -c /opt/h3-runtime/local-runtime-constraints.txt \
       -r custom_nodes/Veda-on-ComfyUI/requirements.txt \
       -r custom_nodes/comfyui-kjnodes/requirements.txt \
       -r custom_nodes/comfyui-minimaxrefpack/requirements.txt \
       -r custom_nodes/comfyui-videohelpersuite/requirements.txt \
       -r custom_nodes/ComfyUI-TensorMaster-Preprocessors/requirements.txt \
       -r custom_nodes/ComfyUI-LTXVideo/requirements.txt \
       omegaconf \
       ultralytics scipy comfy-kitchen==0.2.37 \
       huggingface_hub accelerate \
    && python -c 'import importlib.metadata as m, json; expected=json.load(open("/opt/h3-runtime/local-runtime.json"))["packages"]; mismatch={n:(v,m.version(n)) for n,v in expected.items() if m.version(n)!=v}; assert not mismatch, mismatch; from transformers import CLIPTokenizer' \
    && python -m compileall -q custom_nodes/comfyui-minimaxrefpack custom_nodes/ComfyUI-PromptDirector custom_nodes/h3_mobile

ENV PYTHONUNBUFFERED=1 HF_HUB_DISABLE_TELEMETRY=1 H3_PROFILE=ref2v-int8
CMD ["bash", "/opt/h3-runtime/start.sh"]
