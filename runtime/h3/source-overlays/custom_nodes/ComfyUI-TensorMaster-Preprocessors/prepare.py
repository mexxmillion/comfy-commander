"""A single queued reference step with reusable isolation and depth caches."""
import hashlib
import io
from pathlib import Path
from uuid import uuid4

import numpy as np
import torch
from PIL import Image, ImageOps

from .nodes import TMCompositeReference, TMIsolateReference


def tensor(image):
    return torch.from_numpy(np.array(image, dtype=np.float32) / 255).unsqueeze(0)


def save_tensor(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{uuid4().hex}.png')
    try:
        Image.fromarray((image[0].cpu().numpy().clip(0, 1) * 255).round().astype(np.uint8)).save(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def input_file(name):
    import folder_paths
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / name).resolve()
    if not name.startswith('h3_mobile/') or '\\' in name or '..' in Path(name).parts or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Only uploaded reference/cache images are supported.')
    return path


class TMDepthReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',), 'invert': ('BOOLEAN', {'default': False})}}

    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'depth'
    CATEGORY = 'Tensor Master/Reference preprocessing'

    @torch.inference_mode()
    def depth(self, image, invert=False):
        import comfy.model_management as mm
        import folder_paths
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        model_path = Path(folder_paths.models_dir) / 'tensor-master-preprocessors' / 'depth-anything-v2-small'
        if not (model_path / 'model.safetensors').is_file():
            raise RuntimeError('Depth Anything V2 Small weights missing. Run sync_models.py first.')
        if image.shape[0] != 1 or image.shape[1] * image.shape[2] > 20_000_000:
            raise ValueError('Depth preprocessing accepts one image below 20 megapixels.')
        mm.free_memory(2 * 1024**3, mm.get_torch_device())
        model = inputs = prediction = None
        try:
            processor = AutoImageProcessor.from_pretrained(model_path, local_files_only=True)
            model = AutoModelForDepthEstimation.from_pretrained(model_path, local_files_only=True,
                use_safetensors=True).to(mm.get_torch_device()).eval()
            source = Image.fromarray((image[0, :, :, :3].cpu().numpy().clip(0, 1) * 255).round().astype(np.uint8))
            inputs = processor(images=source, return_tensors='pt').to(mm.get_torch_device())
            prediction = model(**inputs).predicted_depth
            depth = torch.nn.functional.interpolate(prediction.unsqueeze(1), size=image.shape[1:3],
                mode='bicubic', align_corners=False)[0, 0].cpu()
            depth = (depth - depth.min()) / (depth.max() - depth.min()).clamp(min=1e-6)
            if invert:
                depth = 1 - depth
            return (depth.unsqueeze(0).unsqueeze(-1).repeat(1, 1, 1, 3),)
        finally:
            inputs = prediction = None
            if model is not None:
                model.cpu()
            mm.soft_empty_cache()


class TMPrepareReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'source_file': ('STRING', {'default': 'h3_mobile/reference.png'}),
            'isolate': ('BOOLEAN', {'default': True}), 'text': ('STRING', {'default': 'person'}),
            'confidence': ('FLOAT', {'default': 0.25, 'min': 0.05, 'max': 0.95}),
            'selection': (['best', 'all'] + [str(i) for i in range(1, 21)],),
            'depth': ('BOOLEAN', {'default': False}), 'invert_depth': ('BOOLEAN', {'default': False}),
            'background': (['gray', 'white', 'black'],), 'crop': ('BOOLEAN', {'default': True}),
            'use_cache': ('BOOLEAN', {'default': False}),
            }, 'optional': {name: ('STRING', {'default': ''}) for name in
                ('cache_key', 'isolation_key', 'depth_key', 'cached_isolation', 'cached_depth', 'source_sha256')}}

    RETURN_TYPES = ('IMAGE', 'IMAGE', 'MASK')
    RETURN_NAMES = ('rgb_reference', 'full_cutout_rgba', 'full_foreground_mask')
    FUNCTION = 'prepare'
    CATEGORY = 'Tensor Master/Reference preprocessing'

    @classmethod
    def IS_CHANGED(cls, source_file, use_cache=False, **kwargs):
        # Comfy otherwise sees only a filename string and can bypass prepare()
        # entirely when that file has been replaced in place.
        if not use_cache:
            return float('nan')
        with input_file(source_file).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    def prepare(self, source_file, isolate=True, text='person', confidence=0.25, selection='best',
                depth=False, invert_depth=False, background='gray', crop=True, use_cache=False,
                cache_key='', isolation_key='', depth_key='', cached_isolation='', cached_depth='', source_sha256=''):
        import folder_paths
        path = input_file(source_file)
        source_bytes = path.read_bytes()
        content = hashlib.sha256(source_bytes).hexdigest()
        if source_sha256 and content != source_sha256:
            raise ValueError('Reference image changed after this job was submitted. Submit again to process the replacement.')
        # Unverified external keys cannot authorize reuse for a different source.
        if cache_key and not source_sha256:
            cache_key = isolation_key = depth_key = cached_isolation = cached_depth = ''
        with Image.open(io.BytesIO(source_bytes)) as source:
            source = ImageOps.exif_transpose(source).convert('RGB')
            if source.width * source.height > 20_000_000:
                raise ValueError('Resize the reference below 20 megapixels.')
            image = tensor(source)
        # The controller supplies content/model/settings hashes. Direct Comfy use
        # computes its own keys using the same pinned model inventory as a version.
        if not cache_key:
            version = Path(__file__).with_name('models.lock.json').read_bytes()
            isolation_key = hashlib.sha256(version + f'{content}:{text}:{confidence}:{selection}'.encode()).hexdigest()
            depth_key = hashlib.sha256(version + f'{content}:depth:{invert_depth}'.encode()).hexdigest()
            cache_key = hashlib.sha256(f'{content}:{isolate}:{isolation_key}:{depth}:{depth_key}:{background}:{crop}'.encode()).hexdigest()
        for key in (cache_key, isolation_key, depth_key):
            if key and (len(key) != 64 or any(c not in '0123456789abcdef' for c in key)):
                raise ValueError('Invalid preprocessing cache key.')
        output = Path(folder_paths.get_output_directory()) / 'reference-cache'
        files = []

        def cached(name, staged, mode):
            destination = output / name
            if not use_cache:
                return None
            candidate = input_file(staged) if staged else destination
            if not candidate.is_file():
                return None
            with Image.open(candidate) as stored:
                value = tensor(stored.convert(mode))
            print(f'[Tensor Master] Reused {name}', flush=True)
            if not staged:
                files.append({'filename': name, 'subfolder': 'reference-cache', 'type': 'output'})
            return value

        def persist(value, name):
            if use_cache:
                save_tensor(value, output / name)
                files.append({'filename': name, 'subfolder': 'reference-cache', 'type': 'output'})

        if isolate:
            name = f'{isolation_key}-rgba.png'
            rgba = cached(name, cached_isolation, 'RGBA')
            if rgba is None:
                rgba, _ = TMIsolateReference().isolate(image, text, confidence, selection)
                rgba = torch.cat((rgba[..., :3] * rgba[..., 3:4], rgba[..., 3:4]), dim=-1)
                persist(rgba, name)
            if rgba.shape[1:3] != image.shape[1:3]:
                raise ValueError('Cached isolation dimensions do not match the reference.')
            mask = rgba[..., 3:4]
        else:
            mask = torch.ones_like(image[..., :1])
            rgba = torch.cat((image, mask), dim=-1)
        if depth:
            name = f'{depth_key}-depth.png'
            depth_image = cached(name, cached_depth, 'RGB')
            if depth_image is None:
                depth_image = TMDepthReference().depth(image, invert_depth)[0]
                depth_image = (depth_image * 255).round() / 255
                persist(depth_image, name)
            if depth_image.shape[1:3] != image.shape[1:3]:
                raise ValueError('Cached depth dimensions do not match the reference.')
            rgba = torch.cat((depth_image * mask, mask), dim=-1)
        rgb = TMCompositeReference().composite(rgba, background, crop if isolate else False)[0]
        persist(rgb, f'{cache_key}-rgb.png')
        return {'ui': {'reference_cache': files}, 'result': (rgb, rgba, mask[..., 0])}
