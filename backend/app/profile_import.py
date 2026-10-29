"""Human-reviewed H3 profile drafts; imports never execute or upload models."""
import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .community_loras import entries_for_profile
from .rental_ui import require_admin
from .studio_agent import analyze_workflow

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "state" / "community-profiles"
router = APIRouter(prefix="/api/v1/setup/profiles")


class ProfileImport(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_profile: Literal["ref2v-int8", "ref2v-singularity", "fasth3-omni"] = "ref2v-int8"
    workflow: dict[str, Any]


def inspect_profile(payload: ProfileImport) -> dict:
    from .main import validate_h3_graph

    graph = payload.workflow.get("prompt", payload.workflow)
    encoded = json.dumps(payload.workflow)
    if len(encoded) > 4_000_000:
        raise HTTPException(413, "Workflow must be 4 MB or smaller")
    if not isinstance(graph, dict) or not graph or len(graph) > 5000:
        return {"valid": False, "ready": False, "errors": ["Export a ComfyUI API-format H3 workflow (not the canvas JSON)."]}
    sensitive = r'"(?:api_key|openrouter_api_key|hf_token|runpod_api_key|vast_api_key|authorization|password)"\s*:\s*"[^"\s]'
    if re.search(sensitive, encoded, re.IGNORECASE) or re.search(r'\b(?:hf_|sk-)[A-Za-z0-9_-]{20,}', encoded):
        return {"valid": False, "ready": False, "errors": ["Remove credentials from the workflow. Configure them through Setup instead."]}
    errors = []
    for node in graph.values():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            errors.append("Every API node needs a class_type and inputs object. Export in API format.")
            break
        for value in node["inputs"].values():
            if (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
                    and (value[0] not in graph or not isinstance(value[1], int) or value[1] < 0)):
                errors.append("A node link points to a missing node or invalid output index.")
    if not errors:
        try:
            validate_h3_graph(graph)
        except HTTPException as exc:
            errors.append(str(exc.detail))
    analysis = analyze_workflow(graph) or {}
    if not any(node.get("class_type") == "UNETLoader" and
               isinstance(node.get("inputs", {}).get("unet_name"), str) and
               node["inputs"]["unet_name"] for node in graph.values() if isinstance(node, dict)):
        errors.append("The H3 workflow must name its diffusion model in UNETLoader.")
    manifest = json.loads((ROOT / "runtime/h3/models.lock.json").read_text(encoding="utf-8"))
    groups = manifest["profiles"][payload.base_profile]
    artifacts = [a for a in manifest["artifacts"] if a["group"] in groups]
    artifacts += entries_for_profile(payload.base_profile)
    # A model selector uses paths relative to its category, not arbitrary host paths.
    known = set()
    for item in artifacts:
        path = item["file"].replace("\\", "/")
        known.add(path)
        category = item.get("local_dir", ".")
        if category == "." and "/" in path:
            known.add(path.split("/", 1)[1])
        if "/" not in path or category == "loras":
            known.add(path)
    unresolved = [name for name in analysis.get("model_references", []) if name not in known]
    return {"valid": not errors, "ready": not errors and not unresolved,
            "errors": sorted(set(errors)), "analysis": analysis, "unresolved_models": unresolved,
            "upload_question": "Do you need to upload these models or LoRAs to Hugging Face, or do they already have a licensed download source?" if unresolved else None,
            "note": "Rental readiness uses the pinned base runtime plus registered LoRAs. Full model or custom-node dependencies without a reviewed deployment source remain unresolved. No workflow has been run."}


def saved_profiles() -> list[dict]:
    records = []
    for path in sorted(PROFILES.glob("profile-*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            # Revalidate against current manifests, rather than trusting saved readiness.
            payload = ProfileImport(**item)
            report = inspect_profile(payload)
            records.append({"id": path.stem, "name": payload.name,
                            "base_profile": payload.base_profile, **report})
        except (OSError, ValueError, TypeError):
            continue
    return records


def rental_base(profile: str) -> str:
    if profile in {"ref2v-int8", "ref2v-singularity", "fasth3-omni", "qwen-image-21"}:
        return profile
    if not re.fullmatch(r"profile-[a-f0-9]{12}", profile):
        raise HTTPException(422, "Unsupported runtime profile")
    record = next((item for item in saved_profiles() if item["id"] == profile), None)
    if not record or not record["ready"]:
        raise HTTPException(422, "This imported profile has unresolved dependencies. Review it in Setup.")
    return record["base_profile"]


@router.post("/inspect")
def inspect_import(payload: ProfileImport, request: Request):
    require_admin(request, mutation=True)
    return inspect_profile(payload)


@router.get("")
def list_imports(request: Request):
    require_admin(request)
    return {"profiles": saved_profiles()}


@router.post("")
def save_import(payload: ProfileImport, request: Request):
    require_admin(request, mutation=True)
    report = inspect_profile(payload)
    if not report["valid"]:
        raise HTTPException(422, "Fix the workflow validation errors before saving")
    profile_id = f"profile-{uuid4().hex[:12]}"
    PROFILES.mkdir(parents=True, exist_ok=True)
    path = PROFILES / f"{profile_id}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload.model_dump(), stream)
    return {"id": profile_id, **report}
