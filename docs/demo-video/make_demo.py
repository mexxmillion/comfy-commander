from __future__ import annotations

import math
import os
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
FRAMES = WORK / "frames"
CAPTURES = WORK / "captures"
OUT = WORK / "comfy-commander-competition-demo.mp4"
FFMPEG = Path(os.environ.get("TM_FFMPEG", "ffmpeg"))

W, H = 1920, 1080
BG = "#0b0d0f"
PANEL = "#171a1e"
PANEL_2 = "#22262b"
YELLOW = "#eaff34"
WHITE = "#f4f5f6"
MUTED = "#9ba2aa"
GRID = "#20252a"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 80):
        d.line((x, 0, x, H), fill=GRID, width=1)
    for y in range(0, H, 80):
        d.line((0, y, W, y), fill=GRID, width=1)
    return im, d


def label(d: ImageDraw.ImageDraw, text: str, x: int = 105, y: int = 78) -> None:
    d.rounded_rectangle((x, y, x + 365, y + 48), 22, fill=YELLOW)
    d.text((x + 24, y + 10), text.upper(), fill=BG, font=font(21, True))


def title(d: ImageDraw.ImageDraw, text: str, sub: str, y: int = 165) -> None:
    d.multiline_text((105, y), text, fill=WHITE, font=font(72, True), spacing=8)
    d.multiline_text((110, y + 190), sub, fill=MUTED, font=font(32), spacing=10)


def fit_image(path: Path, box: tuple[int, int, int, int], radius: int = 24) -> Image.Image:
    src = Image.open(path).convert("RGB")
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    ratio = min(bw / src.width, bh / src.height)
    nw, nh = int(src.width * ratio), int(src.height * ratio)
    src = src.resize((nw, nh), Image.Resampling.LANCZOS)
    card = Image.new("RGB", (bw, bh), PANEL)
    card.paste(src, ((bw - nw) // 2, (bh - nh) // 2))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw, bh), radius, fill=255)
    card.putalpha(mask)
    return card


def paste_card(im: Image.Image, path: Path, box: tuple[int, int, int, int], radius: int = 24) -> None:
    card = fit_image(path, box, radius)
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sx1, sy1, sx2, sy2 = box
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((sx1 + 12, sy1 + 18, sx2 + 12, sy2 + 18), radius, fill=(0, 0, 0, 145))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    im.paste(shadow, (0, 0), shadow)
    im.paste(card, (sx1, sy1), card)


def node(d: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], heading: str, detail: str = "", accent: bool = False) -> None:
    fill = "#262c20" if accent else PANEL
    outline = YELLOW if accent else "#3a4148"
    d.rounded_rectangle(xy, 24, fill=fill, outline=outline, width=3)
    x1, y1, x2, y2 = xy
    d.text((x1 + 28, y1 + 28), heading, fill=YELLOW if accent else WHITE, font=font(30, True))
    if detail:
        d.multiline_text((x1 + 28, y1 + 78), detail, fill=MUTED, font=font(22), spacing=7)


def arrow(d: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    d.line((*start, *end), fill=YELLOW, width=6)
    ang = math.atan2(end[1] - start[1], end[0] - start[0])
    for off in (2.55, -2.55):
        p = (end[0] + 23 * math.cos(ang + off), end[1] + 23 * math.sin(ang + off))
        d.line((*end, *p), fill=YELLOW, width=6)


def save(im: Image.Image, name: str) -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(FRAMES / name, quality=95)


def build_frames() -> list[Path]:
    desktop = ROOT / "docs/images/generate-desktop.png"
    phone = ROOT / "docs/images/generate-phone.png"
    onboarding = CAPTURES / "onboarding.png"

    im, d = canvas()
    label(d, "Open Source Creative Control")
    d.text((105, 190), "COMFY", fill=YELLOW, font=font(108, True))
    d.text((105, 305), "COMMANDER", fill=WHITE, font=font(108, True))
    d.text((110, 455), "by Tensor Master", fill=MUTED, font=font(40, True))
    d.rounded_rectangle((110, 620, 1200, 752), 34, fill=PANEL, outline="#343a40", width=2)
    d.text((155, 654), "Creativity first.", fill=WHITE, font=font(42, True))
    d.text((520, 654), "Scalable compute at your fingertips.", fill=YELLOW, font=font(42, True))
    d.text((110, 920), "github.com/mexxmillion/tensor-master-community", fill=MUTED, font=font(28))
    save(im, "01-title.png")

    im, d = canvas()
    label(d, "Less setup. More creating.")
    title(d, "ComfyUI power.\nWithout the setup tax.", "Models · nodes · machines · queues")
    x = 1090
    for i, (h, s) in enumerate((("Nodes", "Custom dependencies"), ("Models", "Paths and downloads"), ("Compute", "Workers and rentals"), ("Queues", "Retries and recovery"))):
        node(d, (x, 120 + i * 210, 1780, 285 + i * 210), h, s, i == 3)
    save(im, "02-problem.png")

    im, d = canvas()
    label(d, "A guided first run")
    d.text((105, 156), "Set up once. Create anywhere.", fill=WHITE, font=font(65, True))
    paste_card(im, onboarding, (105, 280, 1815, 975))
    save(im, "03-onboarding.png")

    im, d = canvas()
    label(d, "One portable studio")
    d.text((105, 150), "Desktop · Remote · Phone", fill=WHITE, font=font(62, True))
    paste_card(im, desktop, (105, 280, 1390, 965))
    paste_card(im, phone, (1450, 230, 1785, 965), 38)
    save(im, "04-portable.png")

    im, d = canvas()
    label(d, "Tensor Master Dispatcher")
    d.text((105, 150), "Every generation becomes a durable job.", fill=WHITE, font=font(58, True))
    node(d, (105, 400, 450, 650), "CREATE", "Prompt + profile\n+ inputs")
    node(d, (610, 350, 1050, 700), "DISPATCH", "Queue · Priority\nRouting · Recovery", True)
    node(d, (1220, 250, 1770, 430), "LOCAL GPU", "Your machine")
    node(d, (1220, 510, 1770, 690), "GPU CLUSTER", "Compatible workers")
    node(d, (1220, 770, 1770, 950), "RUNPOD / VAST", "Bounded rentals")
    arrow(d, (450, 525), (610, 525)); arrow(d, (1050, 465), (1220, 340)); arrow(d, (1050, 525), (1220, 600)); arrow(d, (1050, 585), (1220, 860))
    save(im, "05-dispatcher.png")

    im, d = canvas()
    label(d, "Elastic compute")
    d.text((105, 150), "One GPU—or many.", fill=WHITE, font=font(72, True))
    d.text((105, 250), "Capacity grows as far as you authorize.", fill=YELLOW, font=font(45, True))
    for i in range(12):
        col, row = i % 6, i // 6
        x, y = 115 + col * 280, 430 + row * 250
        node(d, (x, y, x + 220, y + 160), f"GPU {i+1:02d}", "READY" if i < 8 else "AVAILABLE", i == 0)
    d.text((110, 945), "Discovery · price ceilings · fleet limits · failure recovery", fill=MUTED, font=font(30))
    save(im, "06-scale.png")

    im, d = canvas()
    label(d, "Curated creative workflows")
    d.text((105, 150), "Focus on the shot, not the graph.", fill=WHITE, font=font(60, True))
    items = [("MiniMax H3", "Image-to-video"), ("Fast H3", "Fast iteration"), ("Qwen Image", "Design and edit"), ("Turbo", "4 and 8 steps")]
    for i, (h, s) in enumerate(items):
        x = 105 + (i % 2) * 880; y = 325 + (i // 2) * 280
        node(d, (x, y, x + 760, y + 210), h, s, i == 0)
    d.rounded_rectangle((105, 900, 1760, 995), 28, fill=YELLOW)
    d.text((155, 927), "ONE-SHOT CHARACTER REPLACEMENT  ·  SAM 3 → TARGET GUIDE → H3", fill=BG, font=font(30, True))
    save(im, "07-workflows.png")

    im, d = canvas()
    label(d, "Agent-friendly by design")
    d.text((105, 150), "Drive the studio with Codex or MCP.", fill=WHITE, font=font(60, True))
    node(d, (105, 385, 500, 690), "AGENT", "Inspect workers\nPrepare jobs\nMonitor progress")
    node(d, (725, 330, 1195, 745), "CONTROLLER", "MCP server\nController API\nPolicy + confirmation", True)
    node(d, (1420, 385, 1815, 690), "FARM", "Local + cloud\nDurable outputs\nEvidence")
    arrow(d, (500, 535), (725, 535)); arrow(d, (1195, 535), (1420, 535))
    d.text((105, 900), "Safety rules stay in the dispatcher—not in the prompt.", fill=MUTED, font=font(34))
    save(im, "08-agent.png")

    im, d = canvas()
    label(d, "Open source · AGPL-3.0")
    d.text((105, 190), "FROM ONE IDEA", fill=MUTED, font=font(34, True))
    d.text((105, 255), "TO ONE SHOT", fill=WHITE, font=font(76, True))
    d.text((105, 355), "OR A COMPLETE FILM.", fill=YELLOW, font=font(76, True))
    d.text((110, 545), "Create from almost anywhere.\nKeep every job under control.\nPut elastic compute at your fingertips.", fill=WHITE, font=font(40), spacing=18)
    d.text((110, 875), "github.com/mexxmillion/tensor-master-community", fill=MUTED, font=font(28))
    d.text((110, 930), "#OpenSourceHiggsfield", fill=YELLOW, font=font(30, True))
    save(im, "09-close.png")
    return sorted(FRAMES.glob("*.png"))


def make_music(path: Path, seconds: float = 120.0, rate: int = 44100) -> None:
    # A quiet, original 100 BPM electronic bed: soft triads, bass pulses, and a tick.
    total = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(rate)
        chunk = bytearray()
        chords = [(110.0, 138.59, 164.81), (98.0, 123.47, 146.83), (82.41, 103.83, 130.81), (98.0, 123.47, 146.83)]
        for i in range(total):
            t = i / rate
            beat = t * 100 / 60
            chord = chords[int(beat // 4) % len(chords)]
            pad = sum(math.sin(2 * math.pi * f * t) for f in chord) / 3
            pulse_phase = beat % 1
            pulse = math.sin(2 * math.pi * chord[0] / 2 * t) * math.exp(-7 * pulse_phase)
            tick = (1 - 2 * ((i * 73) % 97) / 96) * math.exp(-35 * pulse_phase)
            fade = min(1.0, t / 2, (seconds - t) / 3)
            sample = int(max(-1, min(1, (0.10 * pad + 0.12 * pulse + 0.018 * tick) * fade)) * 32767)
            chunk += sample.to_bytes(2, "little", signed=True) * 2
            if len(chunk) >= 65536:
                wav.writeframesraw(chunk); chunk.clear()
        if chunk:
            wav.writeframesraw(chunk)


def run(cmd: list[str]) -> None:
    print(" ".join(str(x) for x in cmd))
    subprocess.run(cmd, check=True)


def render() -> None:
    frames = build_frames()
    voice = WORK / "narration.mp3"
    if not voice.exists():
        raise SystemExit("Missing narration.mp3. Generate it first with edge-tts.")
    music = WORK / "music.wav"
    make_music(music)
    durations = [7, 11, 12, 14, 18, 18, 18, 17, 15]
    concat = WORK / "slides.txt"
    lines: list[str] = []
    for frame, duration in zip(frames, durations):
        lines.append(f"file '{frame.as_posix()}'")
        lines.append(f"duration {duration}")
    lines.append(f"file '{frames[-1].as_posix()}'")
    concat.write_text("\n".join(lines), encoding="utf-8")
    silent = WORK / "slides.mp4"
    run([str(FFMPEG), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", "scale=1920:1080,format=yuv420p,fade=t=in:st=0:d=0.6", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(silent)])
    run([str(FFMPEG), "-y", "-i", str(silent), "-i", str(voice), "-i", str(music), "-filter_complex", "[1:a]volume=1.0[voice];[2:a]volume=0.18[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-16:LRA=7:TP=-1.5[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)])
    print(OUT)


if __name__ == "__main__":
    render()
