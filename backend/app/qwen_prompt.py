"""Server-side prompt rewriting for Pocket Qwen Image 2.1 jobs."""
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from .prompt_secret import read_key


class EnhanceRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    mode: Literal["text-to-image", "image-edit"]
    reference_count: int = Field(default=0, ge=0, le=10)
    aspect_ratio: str = Field(default="1:1 (Square)", max_length=64)
    megapixels: float = Field(default=1, ge=0.1, le=16)
    model: Literal[
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash-lite",
        "x-ai/grok-4.3",
    ] = "google/gemini-3-flash-preview"


def system_prompt(mode: str, reference_count: int) -> str:
    common = """You are the prompt writer for Qwen Image 2.1. Rewrite the user's brief into one production-ready image prompt. Return only the rewritten prompt: no heading, quotes, markdown, analysis, alternatives, or negative prompt. Preserve the user's intent and proper nouns. Add concrete visual direction only where it helps: subject and action, environment, composition, camera/viewpoint, lighting, color, materials, atmosphere, spatial relationships, and exact visible text when requested. Avoid keyword soup, contradictions, generic praise, and unsupported claims."""
    if mode == "image-edit":
        tags = ", ".join(f"<image{i}>" for i in range(1, reference_count + 1))
        return common + f"""

This is an image-edit instruction with {reference_count} ordered reference image(s): {tags}. <image1> is the primary image to edit. Other images are supporting references. Use these exact Qwen reference tags where relevant. State precisely what must change and what must remain unchanged. Preserve identity, layout, pose, framing, lighting, typography, and background unless the user explicitly asks to alter them. You cannot see the images: never name, locate, count, or describe an object, person, material, color, camera view, layout, or other visual fact unless the user wrote it. Refer generically to "the requested subject in <image1>" when needed. Do not turn an edit into a new unrelated scene."""
    return common + """

This is text-to-image with no reference image. Make the result self-contained and visually specific enough to construct the complete scene from an empty canvas. Resolve useful composition and environmental details while leaving deliberately unspecified creative choices flexible. Do not include image-reference tags."""


def clean_result(value: str) -> str:
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    if not value or len(value) > 12000:
        raise ValueError("invalid rewritten prompt")
    return value


async def enhance(payload: EnhanceRequest) -> dict:
    if payload.mode == "text-to-image" and payload.reference_count:
        raise HTTPException(400, "Text-to-image cannot include references")
    if payload.mode == "image-edit" and not payload.reference_count:
        raise HTTPException(400, "Image edit needs at least one reference")
    key = read_key()
    if not key:
        raise HTTPException(409, "Qwen prompt enhancement needs the controller OpenRouter credential")
    context = (
        f"Output canvas: {payload.aspect_ratio}, approximately {payload.megapixels:g} megapixels.\n"
        f"User brief:\n{payload.prompt.strip()}"
    )
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": payload.model,
                    "messages": [
                        {"role": "system", "content": system_prompt(payload.mode, payload.reference_count)},
                        {"role": "user", "content": context},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 2200,
                },
            )
        if response.status_code != 200:
            raise HTTPException(502, f"Prompt provider returned HTTP {response.status_code}")
        rewritten = clean_result(response.json()["choices"][0]["message"]["content"])
        return {"prompt": rewritten, "model": payload.model, "mode": payload.mode}
    except HTTPException:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(502, "Prompt provider returned an invalid response or timed out")
