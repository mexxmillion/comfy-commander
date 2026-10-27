"""Approved Pocket Krea identity-edit graph family."""
from fastapi import HTTPException

from .h3_media import h3_input_paths

MODEL = 'krea2_turbo_int8_convrot.safetensors'
CLIP = 'qwen3vl_4b_fp8_scaled.safetensors'
VAE = 'qwen_image_vae.safetensors'
IDENTITY = 'krea2_identity_edit_v1_2.safetensors'
NODES = {'SaveImage', 'KSampler', 'VAEDecode', 'UNETLoader', 'CLIPLoader', 'VAELoader',
         'LoraLoaderModelOnly', 'LoadImage', 'VAEEncode', 'Krea2EditModelPatch',
         'EmptySD3LatentImage', 'ResolutionSelector', 'Krea2EditGroundedEncode'}


def is_graph(graph):
    return any(node.get('class_type') == 'Krea2EditModelPatch' for node in graph.values())


def validate_graph(graph):
    classes = {node.get('class_type') for node in graph.values()}
    if not 14 <= len(graph) <= 80 or not NODES <= classes or not classes <= NODES | {'TMPrepareReference'}:
        raise HTTPException(422, 'Only the approved Pocket Krea identity edit graph is supported')
    for kind, field, expected in [('UNETLoader', 'unet_name', MODEL),
                                  ('CLIPLoader', 'clip_name', CLIP), ('VAELoader', 'vae_name', VAE)]:
        names = {node.get('inputs', {}).get(field) for node in graph.values() if node.get('class_type') == kind}
        if names != {expected}:
            raise HTTPException(422, 'Unsupported Krea model set')
    loras = {node.get('inputs', {}).get('lora_name') for node in graph.values()
             if node.get('class_type') == 'LoraLoaderModelOnly'}
    if IDENTITY not in loras:
        raise HTTPException(422, 'Krea identity edit requires its identity adapter')
    try:
        if not h3_input_paths(graph):
            raise ValueError('Krea requires a Pocket reference image')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
