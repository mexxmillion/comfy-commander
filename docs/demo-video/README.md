# Competition demo video

The rendered first cut is [`comfy-commander-competition-demo.mp4`](comfy-commander-competition-demo.mp4): 1:53.6, 1920×1080 H.264/AAC, with a neural narration track and an original generated music bed.

Source files:

- `narration.txt` — spoken copy
- `make_demo.py` — Comfy-style title cards, diagrams, music generation and final render
- `captures/onboarding.png` — clean-install onboarding capture
- `../images/generate-desktop.png` and `../images/generate-phone.png` — real product UI captures

To rebuild, install Pillow, `edge-tts` and `imageio-ffmpeg`, generate `narration.mp3` from `narration.txt`, set `TM_FFMPEG` to the `imageio-ffmpeg` binary, then run `make_demo.py`. Generated frames and intermediate audio/video files are intentionally not retained.

The published competition upload should remain under two minutes. Do not add credentials, private addresses, copyrighted footage, celebrity likenesses or unlicensed music.
