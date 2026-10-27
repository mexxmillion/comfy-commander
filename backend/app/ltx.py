"""Untiled LTX 2.5 pixel upscaling and SDR-to-HLG recipes."""
import asyncio
import json
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .config import get_settings
from .rental_ui import require_admin

BASE = 'ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors'
DISTILLED = 'ltx-2.5-22b-distilled-lora-450-bf16.safetensors'
UPSCALER = 'ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors'
HDR = 'ltx-2.5-22b-ic-lora-sdr-to-hdr-1.0.safetensors'
VIDEO_VAE = 'ltx-2.5-video-vae-conv-bf16.safetensors'
AUDIO_VAE = 'ltx-2.5-audio-vae-bf16.safetensors'
TEXT = 'gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors'
SCENE = 'ltx-2.5-22b-ic-lora-sdr-to-hdr-scene-emb.safetensors'
SIGMAS = '1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0'


class RecipeRequest(BaseModel):
    mode: Literal['upscale', 'hdr'] = 'upscale'
    file: str = Field(max_length=200)
    prompt: str = Field(default='Preserve the reference video, natural lighting, natural skin texture, crisp fine detail.', max_length=10000)
    start: float = Field(default=0, ge=0)
    duration: float = Field(default=4, gt=0, le=30)
    seed: int = Field(default=1, ge=0, le=2**63-1)
    policy: Literal['local', 'standard', 'any'] = 'local'


def input_path(file):
    root = get_settings().comfyui_input_dir.resolve()
    path = (root / file).resolve()
    if (not file.startswith('h3_mobile/') or Path(file).suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}
            or '\\' in file or '..' in Path(file).parts
            or not path.is_relative_to(root) or not path.is_file()):
        raise HTTPException(404, 'Upload a source video in Pocket Generate first')
    return path


async def probe(path):
    process = await asyncio.create_subprocess_exec('ffprobe', '-v', 'error',
        '-show_entries', 'stream=codec_type,width,height,r_frame_rate,color_transfer,color_primaries,pix_fmt:format=duration',
        '-of', 'json', str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, _ = await process.communicate()
    if process.returncode:
        raise HTTPException(422, 'Cannot decode this video')
    data = json.loads(output)
    if not data.get('streams') or float(data.get('format', {}).get('duration', 0)) <= 0:
        raise HTTPException(422, 'Video has no usable frames or duration')
    video = next((stream for stream in data['streams'] if stream.get('codec_type') == 'video'), None)
    if video is None:
        raise HTTPException(422, 'Video has no usable frames')
    return dict(video, duration=float(data['format']['duration']), fps=float(Fraction(video['r_frame_rate'])),
                has_audio=any(stream.get('codec_type') == 'audio' for stream in data['streams']))


async def upload(request: Request):
    require_admin(request, mutation=True)
    form = await request.form()
    file = form.get('video')
    if file is None or not hasattr(file, 'read'):
        raise HTTPException(422, 'Choose a video')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}:
        raise HTTPException(422, 'Choose MP4, MOV, MKV or WebM')
    name = 'h3_mobile/ltx_' + uuid4().hex + suffix
    path = get_settings().comfyui_input_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open('wb') as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(413, 'Video exceeds 2 GB')
                destination.write(chunk)
        metadata = await probe(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'file': name, 'name': file.filename, **metadata}


async def prepare(payload):
    source = input_path(payload.file)
    metadata = await probe(source)
    if payload.mode == 'hdr' and metadata.get('color_transfer') in {'arib-std-b67', 'smpte2084'}:
        raise HTTPException(422, 'This source is already HDR; this recipe converts SDR to HLG')
    available = metadata['duration'] - payload.start
    if available <= 0:
        raise HTTPException(422, 'Start time is past the end of the video')
    seconds = min(payload.duration, available)
    frames = max(9, round(seconds * 24 / 8) * 8 + 1)
    padded_width=ceil(metadata['width']/32)*32
    padded_height=ceil(metadata['height']/32)*32
    name = 'h3_mobile/ltx_clip_' + uuid4().hex + '.mp4'
    target = get_settings().comfyui_input_dir / name
    process = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-y', '-ss', str(payload.start),
        '-i', str(source), '-t', str(frames / 24), '-vf',
        f'trim=duration={seconds},fps=24,tpad=stop_mode=clone:stop_duration=1,pad={padded_width}:{padded_height}:0:0',
        '-frames:v', str(frames), '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        str(target), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, error = await process.communicate()
    if process.returncode:
        target.unlink(missing_ok=True)
        raise HTTPException(422, 'Could not prepare source clip: ' + error.decode(errors='replace')[-300:])
    scale = 2 if payload.mode == 'upscale' else 1
    width,height=metadata['width']*scale,metadata['height']*scale
    graph = build_graph(payload, name, padded_width*scale, padded_height*scale, frames,
                        output_width=width,output_height=height, has_audio=metadata['has_audio'])
    recipe = {'mode': 'ltx', 'ltxType': payload.mode, 'sourceFile': payload.file, 'references': [{'kind':'video','file':name}],
              'direction': payload.prompt, 'sourceDirection': payload.prompt, 'seed':payload.seed,
              'duration': frames/24, 'width':width, 'height':height, 'steps':8, 'cfg':1.0,
              'scale':scale, 'tiling':False, 'outputColor': 'HLG BT.2020 10-bit' if payload.mode=='hdr' else 'SDR',
              'renderTarget':payload.policy, 'start':payload.start, 'requestedDuration':payload.duration,
              'sourceWidth':metadata['width'], 'sourceHeight':metadata['height'], 'sourceDuration':metadata['duration']}
    return graph, recipe


def build_graph(payload, file, width, height, frames, *, output_width=None, output_height=None, has_audio=True):
    g = {}
    def node(key, kind, **inputs):
        g[str(key)] = {'class_type': kind, 'inputs': inputs}
    hdr = payload.mode == 'hdr'
    node(1, 'UNETLoader', unet_name=BASE, weight_dtype='default')
    node(2, 'LoraLoaderModelOnly', model=['1',0], lora_name=DISTILLED, strength_model=1.0)
    node(3, 'LTXICLoRALoaderModelOnly', model=['2',0], lora_name=HDR if hdr else UPSCALER, strength_model=1.0)
    node(4, 'LTXVChunkFeedForward', model=['3',0], chunks=8, dim_threshold=4096)
    node(6, 'VAELoader', vae_name=VIDEO_VAE)
    node(7, 'VAELoader', vae_name=AUDIO_VAE)
    if hdr:
        node(5, 'LTXVLoadConditioning', file_name=SCENE, device='cpu')
        node(9, 'ConditioningZeroOut', conditioning=['5',0])
        positive=['5',0];negative=['9',0]
        node(26, 'LTXVVAEForceFloat32', vae=['6',0])
        vae=['26',0]
    else:
        node(5, 'CLIPLoader', clip_name=TEXT, type='ltxv', device='default')
        node(8, 'CLIPTextEncode', clip=['5',0], text=payload.prompt)
        node(9, 'CLIPTextEncode', clip=['5',0], text='')
        positive=['8',0];negative=['9',0];vae=['6',0]
    node(28, 'LTXVUntiledVAE', vae=vae)
    vae=['28',0]
    node(10, 'LTXVConditioning', positive=positive, negative=negative, frame_rate=24.0)
    node(11, 'VHS_LoadVideo', video=file, force_rate=24.0, custom_width=0, custom_height=0,
         frame_load_cap=frames, skip_first_frames=0, select_every_nth=1, format='None')
    image=['11',0]
    if hdr:
        node(25, 'LTXVSDRToHDRWorkingSpace', image=image, color_space='srgb_gamma')
        image=['25',0]
    node(12, 'EmptyLTXVLatentVideo', width=width, height=height, length=frames, batch_size=1)
    node(13, 'LTXAddVideoICLoRAGuide', positive=['10',0], negative=['10',1], vae=vae, latent=['12',0],
         image=image, frame_idx=0, strength=1.0, latent_downscale_factor=['3',1], crop='disabled',
         use_tiled_encode=False, tile_size=256, tile_overlap=64, use_streaming=False, tile_frames=frames)
    node(14, 'LTXVEmptyLatentAudio', frames_number=frames, frame_rate=24.0, batch_size=1, audio_vae=['7',0])
    node(15, 'LTXVConcatAVLatent', video_latent=['13',2], audio_latent=['14',0])
    node(16, 'CFGGuider', model=['4',0], positive=['13',0], negative=['13',1], cfg=1.0)
    node(17, 'RandomNoise', noise_seed=payload.seed)
    node(18, 'KSamplerSelect', sampler_name='euler')
    node(19, 'ManualSigmas', sigmas=SIGMAS)
    node(20, 'SamplerCustomAdvanced', noise=['17',0], guider=['16',0], sampler=['18',0], sigmas=['19',0], latent_image=['15',0])
    node(21, 'LTXVSeparateAVLatent', av_latent=['20',0])
    node(22, 'LTXVCropGuides', positive=['13',0], negative=['13',1], latent=['21',0])
    node(23, 'VAEDecode', samples=['22',2], vae=vae)
    decoded=['23',0]
    if output_width is not None and (output_width,output_height)!=(width,height):
        node(29,'ImageCrop',image=decoded,width=output_width,height=output_height,x=0,y=0)
        decoded=['29',0]
    prefix='ltx/' + payload.mode + '_' + uuid4().hex[:12]
    if hdr:
        node(27, 'LTXVHDRDecodePostprocess', image=decoded, transfer='acescct', exposure=0.0, save_exr=False,
             exr_color_space='acescg', output_dir='hdr_exr', filename_prefix='frame', half_precision=True)
        node(24, 'LTXVSaveHLG', hdr_linear=['27',1], frame_rate=24.0, filename_prefix=prefix, linear_primaries='acescg', audio=['11',2])
    else:
        node(24, 'VHS_VideoCombine', images=decoded, audio=['11',2], frame_rate=24.0, loop_count=0,
             filename_prefix=prefix, format='video/h264-mp4', pix_fmt='yuv420p', crf=14,
             save_metadata=True, trim_to_audio=False, pingpong=False, save_output=True)
    if not has_audio:
        g['24']['inputs'].pop('audio', None)
    return g


def is_graph(graph):
    return any(n.get('class_type') == 'LTXICLoRALoaderModelOnly' and
               n.get('inputs',{}).get('lora_name') in {UPSCALER,HDR} and
               graph.get('24',{}).get('class_type') in {'VHS_VideoCombine','LTXVSaveHLG'} for n in graph.values())


def validate_graph(graph):
    allowed = {'UNETLoader','LoraLoaderModelOnly','LTXICLoRALoaderModelOnly','LTXVChunkFeedForward',
               'CLIPLoader','CLIPTextEncode','VAELoader','LTXVLoadConditioning','ConditioningZeroOut',
               'LTXVVAEForceFloat32','LTXVUntiledVAE','LTXVConditioning','VHS_LoadVideo','LTXVSDRToHDRWorkingSpace',
               'EmptyLTXVLatentVideo','LTXAddVideoICLoRAGuide','LTXVEmptyLatentAudio','LTXVConcatAVLatent',
               'CFGGuider','RandomNoise','KSamplerSelect','ManualSigmas','SamplerCustomAdvanced',
               'LTXVSeparateAVLatent','LTXVCropGuides','VAEDecode','ImageCrop','VHS_VideoCombine','LTXVHDRDecodePostprocess','LTXVSaveHLG'}
    names = {value for n in graph.values() for key,value in n.get('inputs',{}).items()
             if key in {'unet_name','lora_name','vae_name','clip_name','file_name'} and isinstance(value,str)}
    if not 22 <= len(graph) <= 32 or not is_graph(graph) or any(n.get('class_type') not in allowed for n in graph.values()):
        raise HTTPException(422, 'Unsupported LTX recipe graph')
    hdr = graph['24']['class_type']=='LTXVSaveHLG'
    expected={BASE,DISTILLED,VIDEO_VAE,AUDIO_VAE,HDR,SCENE} if hdr else {BASE,DISTILLED,VIDEO_VAE,AUDIO_VAE,UPSCALER,TEXT}
    if names != expected:
        raise HTTPException(422,'Unsupported LTX model set')
    if graph['13']['inputs'].get('use_tiled_encode') or graph['13']['inputs'].get('use_streaming'):
        raise HTTPException(422,'LTX recipes use full-frame conditioning without tiling')
    if graph.get('28',{}).get('class_type') != 'LTXVUntiledVAE':
        raise HTTPException(422,'LTX recipe requires untiled VAE memory policy')
