# Competition demo video

## 55-second cut

`comfy-commander-55sec.mp4` is the condensed dynamic explainer, with a rewritten narration (`narration-short.txt`), seven animated chapters, product screenshots, concept imagery and music. Export: 1920×1080, 24 fps, H.264/AAC, exactly 55 seconds.

To rebuild, generate `narration-short.mp3` using the Andrew multilingual neural voice at +5% rate, mix it with the original generated music into `short-soundtrack.m4a` (55 seconds, -16 LUFS target), then run `python make_dynamic.py --short`.

## Dynamic second cut

`comfy-commander-dynamic-v2.mp4` rebuilds the explainer with continuous motion: particle/grid backgrounds, eased title and panel entrances, animated dispatcher packets, GPU activation, moving UI compositions and chapter fades. It uses the first cut's narration/music. Duration is 1:54; output is 1920×1080 at 24 fps (graphics rendered at 1280×720 and scaled for delivery).

Rebuild with `python make_dynamic.py` after installing Pillow and imageio-ffmpeg. Keep the original MP4 beside the script as the audio source. `creative-studio.png` was created using the built-in image generation tool as concept imagery; it is not a claimed Commander render.

Image prompt: A premium cinematic creative studio in dark graphite, with lime light ribbons connecting a desert film set, lone astronaut, stylized fox sculpture, floating film frames and modular GPU rack; brushed metal, glass, volumetric light, violet accents, left-side negative space; no words, logos or watermark.

The rendered first cut is [`comfy-commander-competition-demo.mp4`](comfy-commander-competition-demo.mp4): 1:53.6, 1920×1080 H.264/AAC, with a neural narration track and an original generated music bed.

Source files:

- `narration.txt` — spoken copy
- `make_demo.py` — Comfy-style title cards, diagrams, music generation and final render
- `captures/onboarding.png` — clean-install onboarding capture
- `../images/generate-desktop.png` and `../images/generate-phone.png` — real product UI captures

To rebuild, install Pillow, `edge-tts` and `imageio-ffmpeg`, generate `narration.mp3` from `narration.txt`, set `TM_FFMPEG` to the `imageio-ffmpeg` binary, then run `make_demo.py`. Generated frames and intermediate audio/video files are intentionally not retained.

The published competition upload should remain under two minutes. Do not add credentials, private addresses, copyrighted footage, celebrity likenesses or unlicensed music.
