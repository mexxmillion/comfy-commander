"""Human-readable summary of what a queued job will render, for the task inspector.

The graph is authoritative for what actually runs; the stored recipe supplies the
operator's intent (original brief, AI prompt, render target).
"""
import json
import math
import re

from sqlalchemy.orm import Session

from . import creative
from .recipes import normalize_recipe
from .store import JobRecipeRow, JobRow, TaskRow


def _resolution(aspect, megapixels, multiple=32):
    """Mirror ComfyUI's ResolutionSelector so the inspector shows the real size."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", str(aspect or ""))
    if not match or not megapixels:
        return None
    w_ratio, h_ratio = float(match[1]), float(match[2])
    scale = math.sqrt(float(megapixels) * 1024 * 1024 / (w_ratio * h_ratio))
    return round(w_ratio * scale / multiple) * multiple, round(h_ratio * scale / multiple) * multiple


def _h3_frames(seconds):
    """H3 samples on a 17k+5 frame grid at 24 fps (see Pocket's ComfyMathExpression)."""
    base = max(5, round(float(seconds) * 24))
    return base + (5 - base % 17) % 17


def _models(graph):
    models, loras = [], []
    for node in graph.values():
        kind, inputs = node.get("class_type"), node.get("inputs", {})
        if kind == "UNETLoader":
            models.append({"role": "Diffusion", "name": inputs.get("unet_name")})
        elif kind == "CLIPLoader":
            models.append({"role": "Text encoder", "name": inputs.get("clip_name")})
        elif kind == "VAELoader":
            models.append({"role": "VAE", "name": inputs.get("vae_name")})
        elif kind in {"LoraLoaderModelOnly", "LTXICLoRALoaderModelOnly"}:
            loras.append({"name": inputs.get("lora_name"), "strength": inputs.get("strength_model")})
        elif kind == "Ref2VAVSAGatePatch":
            loras.append({"name": inputs.get("gate_file"), "sparsity": inputs.get("sparsity")})
        elif kind == "ViggleTurboLora":
            loras.append({"name": inputs.get("lora_name"), "strength": inputs.get("strength")})
    return models, loras


def job_spec(store, task_id):
    with Session(store.engine) as db:
        task = db.get(TaskRow, task_id)
        job = db.get(JobRow, task.job_id) if task else None
        if job is None:
            return None
        saved = db.get(JobRecipeRow, job.id)
        recipe = json.loads(saved.recipe_json) if saved else None
        graph = json.loads(job.graph_json)
    if recipe is None:
        recipe = creative.historic_recipe_index(store, {job.id}).get(job.id) or {}

    def inputs(node_id):
        return graph.get(node_id, {}).get("inputs", {})

    def resolve(value, depth=0):
        if depth > 8 or not isinstance(value, list) or len(value) != 2:
            return value if isinstance(value, (int, float, str)) else None
        node = graph.get(str(value[0]), {})
        data = node.get('inputs', {})
        if node.get('class_type') in {'PrimitiveFloat', 'PrimitiveInt'}:
            return data.get('value')
        if node.get('class_type') == 'ResolutionSelector':
            size = _resolution(data.get('aspect_ratio'), data.get('megapixels'), data.get('multiple') or 8)
            return size[value[1]] if size and value[1] in (0, 1) else None
        if (node.get('class_type') == 'ComfyMathExpression' and value[1] == 1
                and data.get('expression') == 'max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17'):
            seconds = resolve(data.get('values.a'), depth + 1)
            return _h3_frames(seconds) if isinstance(seconds, (int, float)) else None
        return None

    spec = {"job_id": job.id, "workflow": job.workflow, "policy": job.policy,
            "render_target": recipe.get("renderTarget"), "seed": None, "steps": None,
            "sampler": None, "cfg": None, "width": None, "height": None,
            "duration_seconds": None, "frames": None, "fps": None}
    models, loras = _models(graph)
    spec.update(models=models, loras=loras)
    final_prompt = None

    if recipe.get('mode') == 'reference-isolate':
        spec['type'] = 'Reference subject isolation'
        spec['references'] = [{'kind': 'image', 'file': recipe.get('source')}]
        final_prompt = recipe.get('text')
    elif recipe.get('mode') == 'ltx':
        hdr = recipe.get('ltxType') == 'hdr'
        spec['type'] = 'LTX SDR → HLG HDR' if hdr else 'LTX 2× Pixel Upscale'
        size = inputs('12')
        spec.update(width=recipe.get('width',size.get('width')), height=recipe.get('height',size.get('height')), frames=size.get('length'),
                    fps=24, duration_seconds=size.get('length',0)/24, steps=8, cfg=1,
                    seed=inputs('17').get('noise_seed'), sampler='euler',
                    references=recipe.get('references',[]), size_note='Full frame · no tiles · '+recipe.get('outputColor','SDR'))
        final_prompt = recipe.get('direction')
    elif recipe.get('mode') == 'krea':
        spec['type'] = 'Krea 2 Identity Edit'
        spec['references'] = recipe.get('references', [])
        size = _resolution(recipe.get('aspect'), recipe.get('megapixels'), 8)
        if size:
            spec.update(width=size[0], height=size[1])
        sample = inputs('53')
        spec.update(seed=sample.get('seed'), steps=sample.get('steps'), cfg=sample.get('cfg'),
                    sampler=sample.get('sampler_name'))
        final_prompt = inputs('84').get('prompt')
    elif graph.get("4", {}).get("class_type") == "TextEncodeQwenImage21":
        refs = [node["inputs"].get("image") for node in graph.values()
                if node.get("class_type") == "LoadImage"]
        turbo = graph.get("10", {}).get("class_type") == "ViggleTurboLora"
        spec["type"] = f"Qwen Image 2.1 · {'edit' if refs else 'text-to-image'}{' · Turbo' if turbo else ''}"
        spec["references"] = [{"kind": "image", "file": ref} for ref in refs]
        final_prompt = inputs("4").get("prompt")
        sampler = inputs("15" if turbo else "9")
        spec.update(steps=6 if turbo else sampler.get("steps"), cfg=1 if turbo else sampler.get("cfg"),
                    seed=inputs("11").get("noise_seed") if turbo else sampler.get("seed"),
                    sampler="euler")
        size = inputs("5")
        if refs and recipe.get("editCanvas") != "selected":
            spec["size_note"] = "Matches the first reference image"
        else:
            spec.update(width=size.get("width"), height=size.get("height"))
    elif any(n.get('class_type') in {'MiniMaxH3ImageToVideo', 'MiniMaxH3ReferenceToVideo'} for n in graph.values()):
        h3_id = next(key for key, n in graph.items() if n.get('class_type') in {'MiniMaxH3ImageToVideo', 'MiniMaxH3ReferenceToVideo'})
        h3_inputs = inputs(h3_id)
        frames_mode = graph[h3_id]['class_type'] == 'MiniMaxH3ImageToVideo'
        still = graph.get("155", {}).get("class_type") == "SaveImage"
        label = "H3 first/last frame" if frames_mode else "H3 reference video"
        spec["type"] = f"{label}{' · still image' if still else ''}"
        if frames_mode:
            spec["references"] = [{"kind": kind, "file": inputs(node).get("image")}
                                  for kind, node in (("first_frame", "183"), ("last_frame", "184"))
                                  if inputs(node).get("image")]
            literal = inputs("131").get("prompt")
            final_prompt = literal if isinstance(literal, str) else None
        else:
            try:
                spec["references"] = json.loads(inputs("181").get("references_json") or "{}").get("references", [])
            except ValueError:
                spec["references"] = []
            if inputs("181").get("prompt_provider") == "none":
                final_prompt = inputs("181").get("direction")
            spec["job_type"] = inputs("181").get("job_type")
        size = _resolution(inputs("162").get("aspect_ratio"), inputs("162").get("megapixels"),
                           inputs("162").get("multiple") or 32)
        if size:
            spec["width"], spec["height"] = size
        spec.update(aspect=inputs("162").get("aspect_ratio"), megapixels=inputs("162").get("megapixels"),
                    steps=inputs("124").get("steps"), sampler=inputs("142").get("sampler_name"),
                    scheduler=inputs("124").get("scheduler"), seed=inputs("129").get("noise_seed"))
        width, height = resolve(h3_inputs.get('width')), resolve(h3_inputs.get('height'))
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            spec.update(width=width, height=height, actual_megapixels=round(width*height/(1024*1024), 3),
                        encoded_aspect=f'{int(width)//math.gcd(int(width),int(height))}:{int(height)//math.gcd(int(width),int(height))}')
        seconds = inputs("133").get("value")
        spec['requested_duration_seconds'] = seconds
        if isinstance(seconds, (int, float)):
            spec["frames"] = 1 if still else _h3_frames(seconds)
            spec["fps"] = None if still else 24
            spec["duration_seconds"] = None if still else round(spec["frames"] / 24, 2)
        frames = resolve(h3_inputs.get('length'))
        if not still and isinstance(frames, (int, float)):
            spec.update(frames=_h3_frames(frames/24), fps=24, duration_seconds=round(_h3_frames(frames/24)/24, 2))
        if spec.get('job_type') == 'replacement':
            spec['type'] = 'H3 Motion control · SAM3 → H3'
            spec['video_preparation'] = 'Target size · 24 fps · requested frame window' if 'replacement_video_prepare' in graph else 'Original reference resolution'
        spec['frame_note'] = 'Output frames; SAM3 source-tracking frames are a separate stage.'
    elif any(node.get("class_type") == "H3FaceTrackCrop" for node in graph.values()):
        nodes = {node.get("class_type"): node.get("inputs", {}) for node in graph.values()}
        spec["type"] = "H3 Face Refine"
        spec["references"] = [{"kind": "video", "file": nodes.get("VHS_LoadVideo", {}).get("video")},
                              {"kind": "image", "file": nodes.get("LoadImage", {}).get("image")}]
        final_prompt = nodes.get("MiniMaxH3ReferenceToVideo", {}).get("prompt")
        canvas = nodes.get("H3FaceTrackCrop", {})
        spec.update(steps=nodes.get("BasicScheduler", {}).get("steps"),
                    sampler=nodes.get("KSamplerSelect", {}).get("sampler_name"),
                    seed=nodes.get("RandomNoise", {}).get("noise_seed"),
                    fps=24, size_note=f"Source video size · face crop {canvas.get('canvas_width')} px"
                    f" · denoise {nodes.get('BasicScheduler', {}).get('denoise')}")
        cap = nodes.get("VHS_LoadVideo", {}).get("frame_load_cap")
        if cap:
            spec.update(frames=cap, duration_seconds=round(cap / 24, 2))
    else:
        spec["type"] = job.workflow
        spec["references"] = []

    # Legacy selector titles include "Portrait Widescreen". Describe the actual
    # output canvas instead, so those titles cannot mislabel a portrait job.
    width, height = spec.get("width"), spec.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
        orientation = "Portrait" if height > width else "Landscape" if width > height else "Square"
        spec["aspect_label"] = orientation

    recipe = normalize_recipe(recipe)
    original = recipe.get("sourceDirection", recipe.get("direction")) or ""
    ai_prompt = recipe.get("aiPrompt") or (final_prompt if final_prompt and final_prompt != original else "")
    spec.update(prompt=original, ai_prompt=ai_prompt if ai_prompt != original else "",
                enhance=recipe.get("enhancePrompt"), ai_model=recipe.get("aiModel"),
                creative=bool(recipe.get("creative")))
    return spec
