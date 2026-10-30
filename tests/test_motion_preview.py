import asyncio
import hashlib
import subprocess

from backend.app import generator_prompt as prompts
from backend.app.generator_prompts.refs import ReferenceSet


def test_motion_reference_limits_window_and_passes_exact_target_size(monkeypatch, tmp_path):
    source=tmp_path/'source.mp4'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(prompts, 'reference_path', lambda _:source)
    calls=[]
    async def convert(path, kind, crop, trim, **kwargs):
        calls.append((trim,kwargs))
        return b'preview','video/mp4'
    monkeypatch.setattr(prompts, 'convert_media', convert)
    refs=ReferenceSet.from_obj([{'kind':'video','file':'h3_mobile/source.mp4','trim':[5,20]}])
    parts,manifest=asyncio.run(prompts.reference_content(refs,3,(544,960)))
    assert calls==[([5.0,8.0],{'preview':True,'target_size':(544,960)})]
    assert '5–8 seconds' in str(manifest)
    assert parts[-1]['type']=='video_url'


def test_compressed_preview_uses_requested_dimensions_duration_and_keeps_source(tmp_path):
    import imageio_ffmpeg
    ffmpeg=prompts.ffmpeg_executable()
    source=tmp_path/'source.mp4'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=blue:s=320x180:r=24',
                    '-t','6','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    data,mime=asyncio.run(prompts.convert_media(source,'video',trim=[2,3.5],preview=True,target_size=(256,448)))
    output=tmp_path/'preview.mp4'
    output.write_bytes(data)
    reader=imageio_ffmpeg.read_frames(str(output));meta=next(reader);frame=next(reader);reader.close()
    assert meta['size']==(256,448)
    assert abs(meta['duration']-1.5)<.15
    assert meta['fps']==8
    # Portrait AI previews must preserve the wide source, matching video prep:
    # black padding at the top rather than silently cropping the performer.
    assert max(frame[:3]) < 10
    center = (224 * 256 + 128) * 3
    assert frame[center+2] > 200
    assert len(data)<8*1024**2 and mime=='video/mp4'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
