"""Creative Engine: durable H3 concepts and atomic, repeat-safe batch dispatch."""
import copy
import asyncio
import base64
import io
import json
import logging
import re
import secrets
from threading import Lock
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from PIL import Image, ImageOps, UnidentifiedImageError

from .prompt_secret import read_key
from .config import get_settings
from .store import DEFAULT_MIN_VRAM_GB, Base, JobRecipeRow, JobRow, TaskRow, now
from .creative_prompts import PROMPT_VERSION, generation_mode, system_prompt
from .creative_prompts import idea_system_prompt, H3_SKIN_COLOR_ARTIFACT_GUARD, H3_FACIAL_ACTION_ARTIFACT_GUARD
from . import generator_prompt
from . import character_replacement

QUEUE_LOCK = Lock()
LOG = logging.getLogger(__name__)


class CreativeBatchRow(Base):
    __tablename__ = "creative_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(64))
    data_json: Mapped[str] = mapped_column(Text)


class Brief(BaseModel):
    brief: str = Field(min_length=1, max_length=12000)
    count: int = Field(default=5, ge=2, le=8)
    direction: str = Field(default="", max_length=6000)
    reference_notes: str = Field(default="", max_length=6000)
    model: Literal[
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash-lite",
        "x-ai/grok-4.3",
    ] = "google/gemini-3-flash-preview"
    recipe: dict
    graph: dict
    policy: Literal["local", "standard", "any"] = "local"
    parent: str | None = Field(default=None, max_length=128)


class Concept(BaseModel):
    index: int = Field(ge=0, le=7)
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=24000)


class Selection(BaseModel):
    concepts: list[Concept] = Field(min_length=1, max_length=8)


def reference_images(references: list) -> list:
    if len(references) > 15:
        raise HTTPException(400, "At most 15 references are supported")
    root = get_settings().comfyui_input_dir.resolve()
    content = []
    counts = {}
    for ref in references:
        kind = ref.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in {"image", "first_frame", "last_frame"}:
            continue
        name = ref.get("file")
        if not isinstance(name, str) or not name.startswith("h3_mobile/") or "\\" in name:
            raise HTTPException(400, "Only Pocket-uploaded images may be sent to Creative Engine")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(400, "Reference image is unavailable")
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if ref.get('crop'):
                    x, y, w, h = ref['crop']
                    image = image.crop((round(x*image.width), round(y*image.height),
                                        round((x+w)*image.width), round((y+h)*image.height)))
                image.thumbnail((1280, 1280))
                output = io.BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=80)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(400, "Reference image could not be read")
        content.extend([{"type":"text", "text":f"Reference {kind} {counts[kind]}"},
                        {"type":"image_url", "image_url":{"url":"data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()}}])
    return content


def _message_text(content) -> str:
    """Normalize OpenRouter's string or typed-block message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "\n".join(parts)
    raise ValueError("Provider message did not contain text")


def _parse_concepts(content, count: int) -> list[dict]:
    text = _message_text(content).strip()
    # Gemini occasionally wraps JSON despite response_format=json_object.
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Provider reply did not contain a JSON object")
        data = json.loads(text[start:end + 1])
    concepts = data.get("concepts") if isinstance(data, dict) else None
    if not isinstance(concepts, list) or len(concepts) != count:
        raise ValueError(f"Provider returned {len(concepts) if isinstance(concepts, list) else 0} of {count} concepts")
    validated = []
    for i, item in enumerate(concepts):
        if not isinstance(item, dict):
            raise ValueError("Provider returned a non-object concept")
        item = {k: v for k, v in item.items() if k != "index"}
        validated.append(Concept(index=i, **item).model_dump())
    if len({c["prompt"].strip().casefold() for c in validated}) != count:
        raise ValueError("Provider returned duplicate prompts")
    return validated


async def _develop_ideas(payload: Brief) -> list[dict]:
    try:
        key = read_key()
    except RuntimeError as exc:
        raise HTTPException(503, "Saved OpenRouter credential cannot be loaded. Run Restart All AI Services.bat on the host PC.") from exc
    if not key:
        raise HTTPException(409, "Creative Engine needs the controller OpenRouter credential")
    references = payload.recipe.get("references", [])
    counts = {}
    roles = []
    for ref in references:
        kind = ref.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        tag = {"image": "Picture", "video": "Video", "audio": "Audio"}.get(kind)
        roles.append(f"<{tag} {counts[kind]}> ({'image thumbnail supplied' if kind == 'image' else 'NOT sent; use reference notes only'})" if tag else f"{kind} (literal endpoint image supplied)")
    from .reference_preprocess import role_notes
    context = {"brief": payload.brief, "count": payload.count,
               "creative_direction": payload.direction, "reference_notes": payload.reference_notes,
               "mode": generation_mode(payload.recipe), "reference_roles": roles,
               'reference_preprocessing': role_notes(references),
               "output_kind": "still-image" if payload.recipe.get("singleFrame") else "video",
               "duration_seconds": None if payload.recipe.get("singleFrame") else payload.recipe.get("duration"),
               "aspect": payload.recipe.get("aspect")}
    images = await asyncio.to_thread(reference_images, references)
    request_json = {"model": payload.model, "messages": [
        {"role": "system", "content": (system_prompt(payload.recipe) if payload.recipe.get("singleFrame") else idea_system_prompt()) +
         f'\nGenerate exactly {payload.count} complete, distinct concepts in the concepts array. The requested count applies to concepts, not shots inside one concept.'},
        {"role": "user", "content": [{"type":"text", "text":json.dumps(context)}, *images]}],
        "temperature": 1, "max_tokens": min(24000, 1200 + payload.count * 2400),
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "creative_concepts", "strict": True, "schema": {
                "type": "object", "additionalProperties": False, "required": ["concepts"],
                "properties": {"concepts": {"type": "array", "minItems": payload.count, "maxItems": payload.count,
                    "items": {"type": "object", "additionalProperties": False, "required": ["title", "prompt"],
                              "properties": {"title": {"type": "string"}, "prompt": {"type": "string"}}}}}}}}}
    last_error = None
    timeout = httpx.Timeout(150, connect=20)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                response = await client.post("https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"}, json=request_json)
                if response.status_code == 400 and attempt == 0 and any(
                        marker in response.text.lower() for marker in ('json_schema', 'response_format', 'structured output')):
                    request_json['response_format'] = {'type': 'json_object'}
                    continue
                if response.status_code != 200:
                    if response.status_code not in {408, 409, 429} and response.status_code < 500:
                        raise HTTPException(502, f"Creative provider returned HTTP {response.status_code}; no renders queued")
                    raise httpx.HTTPStatusError(
                        f"transient provider HTTP {response.status_code}", request=response.request, response=response)
                content = response.json()["choices"][0]["message"]["content"]
                return _parse_concepts(content, payload.count)
            except HTTPException:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError, TypeError, ValidationError) as error:
                last_error = error
                LOG.warning("Creative provider attempt %s/2 failed: %s: %s",
                            attempt + 1, type(error).__name__, str(error)[:240])
                if attempt == 0:
                    request_json["messages"] = [*request_json["messages"], {
                        "role": "user",
                        "content": ("Your previous reply could not be parsed: " + str(error)[:500] +
                                    ". Return exactly the requested number of complete, distinct concepts "
                                    "as one JSON object with a concepts array; no markdown or commentary.")
                    }]
    if isinstance(last_error, httpx.TimeoutException):
        reason = "timed out twice"
    else:
        detail = str(last_error).strip()[:500] if last_error else "unknown validation failure"
        reason = f"output failed validation twice: {detail}"
    raise HTTPException(502, f"Creative provider {reason}; no renders queued. Please retry.")


async def develop(payload: Brief) -> list[dict]:
    ideas = await _develop_ideas(payload)
    # Still generation keeps its dedicated image writer, not the audiovisual enhancer.
    if payload.recipe.get("singleFrame"):
        return ideas
    recipe = payload.recipe
    semaphore = asyncio.Semaphore(2)

    async def expand(idea):
        direction = (
            f"ORIGINAL USER BRIEF (binding constraints):\n{payload.brief}\n\n"
            f"CREATIVE DIRECTION (binding constraints):\n{payload.direction}\n\n"
            f"REFERENCE ROLES / USER NOTES:\n{payload.reference_notes}\n\n"
            f"SELECTED IDEA TO EXPAND:\n{idea['prompt']}\n\n"
            "Write this ONE variation using the normal H3 enhancement rules. "
            "Keep its distinct idea; resolve any conflict in favor of the original "
            "brief, creative direction and reference roles. Do not force a fixed "
            "beat grid or add cuts/dialogue when the brief forbids them.\n"
            + H3_SKIN_COLOR_ARTIFACT_GUARD + H3_FACIAL_ACTION_ARTIFACT_GUARD
        )
        try:
            request = generator_prompt.EnhanceRequest(
                prompt=direction, references=recipe.get("references", []),
                frame_mode=recipe.get("h3Type") != "reference",
                job_type=recipe.get("jobType", "auto"),
                aspect_ratio=recipe.get("aspect", "16:9 (Landscape)"),
                megapixels=recipe.get("megapixels", 0.7),
                duration=recipe.get("duration", 5), model=payload.model,
            )
            async with semaphore:
                result = await generator_prompt.enhance(request)
            final = Concept(index=idea['index'], title=idea['title'], prompt=result['prompt']).model_dump()
            return {**final, 'idea': idea['prompt'], 'enhancement_model': result['model'],
                    'enhancement_mode': result['mode']}
        except (HTTPException, ValidationError, KeyError, TypeError) as error:
            detail = error.detail if isinstance(error, HTTPException) else str(error)
            raise HTTPException(502, f"Enhancement failed for variation {idea['index'] + 1} ({idea['title']}): {detail}; no renders queued") from error

    # Finish all in-flight calls before returning a failure; never queue half a batch.
    results = await asyncio.gather(*(expand(idea) for idea in ideas), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if len({c['prompt'].strip().casefold() for c in results}) != len(results):
        raise HTTPException(502, "Enhancer returned duplicate variations; no renders queued")
    return results


def final_graph(template: dict, prompt: str, seed: int) -> dict:
    graph = copy.deepcopy(template)
    graph["129"]["inputs"]["noise_seed"] = seed
    if graph["131"]["class_type"] == "MiniMaxH3ImageToVideo":
        graph["131"]["inputs"]["prompt"] = prompt
        graph.pop("185", None)
    else:
        graph["181"]["inputs"].update(direction=prompt, prompt_provider="none", openrouter_api_key="")
        # Send the approved text directly; ReferencePack still supplies media.
        graph["131"]["inputs"]["prompt"] = prompt
        graph.pop("182", None)
    return graph


def save_draft(store, payload: Brief, concepts: list[dict], runtime: str) -> dict:
    batch = payload.model_dump()
    batch.update(id=f"creative-{uuid4().hex[:16]}", created_at=now().isoformat(), runtime=runtime, prompt_version=PROMPT_VERSION,
                 concepts=[dict(c, job_id=None) for c in concepts])
    # Guarantee distinct render seeds within this batch.
    base_seed = secrets.randbelow(2**48)
    for i, concept in enumerate(batch["concepts"]):
        concept["seed"] = base_seed + i
    with Session(store.engine) as db, db.begin():
        db.add(CreativeBatchRow(id=batch["id"], created_at=batch["created_at"], data_json=json.dumps(batch)))
    return batch


def read_batch(store, batch_id: str) -> dict:
    with Session(store.engine) as db:
        row = db.get(CreativeBatchRow, batch_id)
        if row is None:
            raise HTTPException(404, "Creative batch not found")
        batch = json.loads(row.data_json)
    for concept in batch["concepts"]:
        concept["job"] = store.job(concept["job_id"]) if concept["job_id"] else None
    return batch


def recent_batches(store) -> list[dict]:
    with Session(store.engine) as db:
        rows = db.scalars(select(CreativeBatchRow).order_by(CreativeBatchRow.created_at.desc()).limit(50)).all()
        return [{"id": r.id, "created_at": r.created_at, "brief": json.loads(r.data_json)["brief"]} for r in rows]


def queue_batch(store, batch_id: str, selection: Selection, validate_graph) -> dict:
    if len({c.index for c in selection.concepts}) != len(selection.concepts):
        raise HTTPException(400, "Select each concept only once")
    with QUEUE_LOCK, Session(store.engine) as db, db.begin():
        row = db.scalar(select(CreativeBatchRow).where(CreativeBatchRow.id == batch_id).with_for_update())
        if row is None:
            raise HTTPException(404, "Creative batch not found")
        batch = json.loads(row.data_json)
        for edit in selection.concepts:
            if edit.index >= len(batch["concepts"]):
                raise HTTPException(400, "Unknown concept")
            concept = batch["concepts"][edit.index]
            if concept["job_id"]:
                continue
            concept.update(title=edit.title, prompt=edit.prompt)
            graph = final_graph(batch["graph"], edit.prompt, concept["seed"])
            if (any(node.get("class_type") == "MiniMaxH3ReferencePack"
                    and node.get("inputs", {}).get("job_type") == "replacement" for node in graph.values())
                    and not character_replacement.is_chain(graph)):
                graph = character_replacement.compose_chain(graph, batch["recipe"].get("replacementTarget", "person"))
            validate_graph(graph)
            job_id = f"job-{uuid4().hex[:12]}"
            recipe = dict(batch["recipe"], direction=batch["brief"], sourceDirection=batch["brief"],
                          aiPrompt=edit.prompt, seed=concept["seed"], enhancePrompt=False,
                          renderTarget="tensor-master-any" if batch["policy"] == "any" else "tensor-master-cloud" if batch["policy"] == "standard" else "tensor-master",
                          creative={"batch_id": batch_id, "index": edit.index, "title": edit.title,
                                    "brief": batch["brief"], "parent": batch["parent"], "direction": batch["direction"],
                                    "idea": concept.get("idea"), "prompt_version": batch.get("prompt_version")})
            concept.update(job_id=job_id, recipe=recipe, graph=graph)
            db.add(JobRow(id=job_id, name=f"CM {batch_id[-6:]} · {edit.index+1} · {edit.title}"[:120],
                          workflow="pocket-h3", policy=batch["policy"], min_vram_gb=DEFAULT_MIN_VRAM_GB,
                          architecture="any", runtime=batch["runtime"], graph_json=json.dumps(graph), created_at=now().isoformat()))
            db.add(TaskRow(id=f"{job_id}-t001", job_id=job_id, number=1))
            db.add(JobRecipeRow(job_id=job_id, recipe_json=json.dumps(recipe)))
            store._event(db, "job", f"{job_id} submitted by Creative Engine {batch_id}")
        row.data_json = json.dumps(batch)
    return read_batch(store, batch_id)


def historic_recipe_index(store, job_ids: set[str]) -> dict[str, dict]:
    """Recover recipes for creative jobs queued before JobRecipeRow was added."""
    found = {}
    if not job_ids:
        return found
    with Session(store.engine) as db:
        rows = db.scalars(select(CreativeBatchRow)).all()
        for row in rows:
            for concept in json.loads(row.data_json).get("concepts", []):
                job_id = concept.get("job_id")
                if job_id in job_ids and isinstance(concept.get("recipe"), dict):
                    found[job_id] = concept["recipe"]
            if len(found) == len(job_ids):
                break
    return found
