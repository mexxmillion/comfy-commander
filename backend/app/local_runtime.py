"""Confirmed, isolated Windows runtime preparation. No rental or render tools."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.h3.build_community import RUNTIME, fetch_source
from .config import get_settings
from .local_models import SETS, required_node_packages
from .rental_ui import require_admin

router = APIRouter(prefix="/api/v1/setup/local-runtime")
LOCK = Lock()
STATE = {"status": "idle"}
BASE_PROFILES = {"h3", "qwen", "motion", "frames", "omni", "singularity"}


def operation_file():
    return get_settings().preferences_file.with_name("local-runtime-operation.json")


def persist():
    path = operation_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        json.dump(STATE, temporary)
        saved = Path(temporary.name)
    saved.replace(path)


def read_operation():
    path = operation_file()
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": "idle"}
    if data.get("status") == "preparing" and data.get("pid"):
        import psutil
        try:
            command = psutil.Process(data["pid"]).cmdline()
            alive = "backend.local_runtime_install" in command
        except psutil.Error:
            alive = False
        if not alive:
            data.update(status="failed", phase="Runtime setup was interrupted. Review the managed destination and retry to repair it; existing model data is retained.")
    return data


class RuntimeSelection(BaseModel):
    destination: str = Field(min_length=1, max_length=2048)
    model_set: str = "h3"
    confirmation: str | None = None


def destination_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or path.resolve() != path or path.parent == path:
        raise ValueError("Choose an absolute, non-redirected folder for a separate Commander runtime.")
    marker = path / ".commander-runtime.json"
    for name in (".commander-runtime.json", ".venv", "models", "input", "output", "user", "source-backups", "commander-constraints.txt", "commander-requirements.txt", "commander-runtime-packages.json", "Start ComfyUI.cmd"):
        if not (path / name).resolve().is_relative_to(path):
            raise ValueError("A managed runtime path redirects outside its installation.")
    if path.exists() and any(path.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("managed_by") != "comfy-commander":
            raise ValueError("Choose an empty folder. Existing personal ComfyUI installations are never overwritten.")
    return path


def plan(selection):
    if selection.model_set not in BASE_PROFILES:
        raise ValueError("Choose a base runtime profile first; add optional model sets afterward.")
    target = destination_path(selection.destination)
    sources = json.loads((RUNTIME / "sources.lock.json").read_text(encoding="utf-8"))
    python = json.loads((RUNTIME / "python-runtime.lock.json").read_text(encoding="utf-8"))
    names = required_node_packages(selection.model_set)
    components = [{"name": "ComfyUI", "revision": sources["comfyui"]["revision"], "url": sources["comfyui"]["url"]}]
    for name in names:
        item = sources["custom_nodes"].get(name, {})
        components.append({"name": name, "revision": item.get("revision") or item.get("version") or "bundled reviewed source",
                           "url": item.get("url") or "https://github.com/mexxmillion/tensor-master-community/tree/main/runtime/h3"})
    digest = hashlib.sha256((str(target) + selection.model_set + json.dumps(sources, sort_keys=True) + json.dumps(python, sort_keys=True)).encode()).hexdigest()
    return {"destination": str(target), "model_set": selection.model_set, "components": components,
            "packages": python["packages"], "confirmation": digest,
            "existing": (target / ".commander-runtime.json").is_file(),
            "notice": "Creates or repairs only a Commander-managed runtime with its own Python environment. Downloads several GB of software; reserve at least 20 GB plus model space. Existing personal ComfyUI is unchanged. Model weights, credentials, worker startup, renders and rentals are separate actions. Stop this managed ComfyUI before updating."}


def run(command, cwd):
    # Never return subprocess output to the browser: package errors may include
    # machine paths or credential-bearing environment configuration.
    result = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("A pinned runtime dependency could not be installed.")


def phase(message):
    with LOCK:
        STATE["phase"] = message
        persist()


def prepare(selection):
    try:
        data = plan(selection)
        root = destination_path(data["destination"])
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ".commander-runtime.json"
        marker.write_text(json.dumps({"managed_by": "comfy-commander", "status": "preparing"}), encoding="utf-8")
        sources = json.loads((RUNTIME / "sources.lock.json").read_text(encoding="utf-8"))
        # Detect a running managed interpreter before touching files. Do not
        # stop it or any personal ComfyUI process automatically.
        import psutil
        for process in psutil.process_iter(["exe"]):
            executable = process.info.get("exe")
            try:
                runs_here = executable and Path(executable).name.lower().startswith("python") and Path(process.cwd()).resolve() == root
            except (psutil.Error, OSError):
                runs_here = False
            if executable and (Path(executable).resolve().is_relative_to(root) or runs_here):
                raise RuntimeError("Stop the managed runtime before updating.")
        phase("Downloading pinned ComfyUI and custom-node sources")
        cache = get_settings().preferences_file.parent / "runtime-source-cache"
        with tempfile.TemporaryDirectory(prefix="commander-install-", dir=root) as temporary:
            stage = Path(temporary).resolve()
            if stage.parent != root:
                raise ValueError("Unsafe source staging path")
            core = stage / "ComfyUI"
            fetch_source(sources["comfyui"], core, cache)
            shutil.copytree(RUNTIME / "source-overlays/comfy", core / "comfy", dirs_exist_ok=True)
            for name in required_node_packages(selection.model_set):
                target = core / "custom_nodes" / name
                item = sources["custom_nodes"].get(name)
                if not item:
                    shutil.copy2(RUNTIME / name, target)
                elif item.get("type") == "snapshot":
                    for relative, expected in item.get("files", {}).items():
                        content = (RUNTIME / item["overlay"] / relative).read_bytes().replace(b"\r\n", b"\n")
                        if hashlib.sha256(content).hexdigest() != expected:
                            raise ValueError("Bundled node source integrity check failed")
                    shutil.copytree(RUNTIME / item["overlay"], target)
                else:
                    fetch_source(item, target, cache)
                if item and item.get("patch"):
                    run(["git", "init", "-q", str(target)], root)
                    run(["git", "-C", str(target), "apply", "--ignore-whitespace", str(RUNTIME / item["patch"])], root)
            # Keep previous managed source files as a recovery copy. Models,
            # input/output/user data and the isolated venv are never replaced.
            backup = root / "source-backups" / stage.name
            for source in core.rglob("*"):
                relative = source.relative_to(core)
                if not source.is_file() or relative.parts[0] in {"models", "input", "output", "user", ".git"} or ".git" in relative.parts:
                    continue
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("Runtime target redirects outside the managed installation")
                if target.is_file() and target.read_bytes() != source.read_bytes():
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        for name in ("models", "input", "output", "user"):
            (root / name).mkdir(exist_ok=True)
        phase("Creating the isolated Python environment")
        python = root / ".venv/Scripts/python.exe"
        if not python.is_file():
            run([sys.executable, "-m", "venv", str(root / ".venv")], root)
        pins = data["packages"]
        constraints = root / "commander-constraints.txt"
        constraints.write_text("".join(f"{name}=={version}\n" for name, version in pins.items()), encoding="utf-8")
        phase("Installing pinned Windows CUDA PyTorch wheels")
        run([str(python), "-m", "pip", "install", "--only-binary=:all:", "--index-url", "https://download.pytorch.org/whl/cu130",
             *[f"{name}=={pins[name]}" for name in ("torch", "torchvision", "torchaudio")]], root)
        phase("Installing ComfyUI and selected custom-node dependencies")
        requirements = root / "commander-requirements.txt"
        requirements.write_text("\n".join(line for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
                                          if not line.startswith("comfyui-workflow-templates==")), encoding="utf-8")
        command = [str(python), "-m", "pip", "install", "-c", str(constraints), "-r", str(constraints), "-r", str(requirements)]
        for name in required_node_packages(selection.model_set):
            dependency = root / "custom_nodes" / name / "requirements.txt"
            if dependency.is_file():
                command.extend(["-r", str(dependency)])
        command.extend(["ultralytics", "scipy", "imageio-ffmpeg", "httpx", "websockets", "pydantic-settings"])
        run(command, root)
        phase("Verifying the isolated Python packages")
        (root / "commander-runtime-packages.json").write_text(json.dumps(pins), encoding="utf-8")
        run([str(python), "-c", "import importlib.metadata as m,json,torch,transformers,comfy; p=json.load(open('commander-runtime-packages.json')); assert all(m.version(k)==v for k,v in p.items()), 'Pinned package mismatch'; assert torch.cuda.is_available(), 'CUDA is unavailable'"], root)
        (root / "Start ComfyUI.cmd").write_text('@echo off\ncd /d "%~dp0"\n".venv\\Scripts\\python.exe" main.py --listen 127.0.0.1 --port 8188\nif errorlevel 1 pause\n', encoding="utf-8")
        marker.write_text(json.dumps({"managed_by": "comfy-commander", "status": "prepared", "model_set": selection.model_set,
                                      "comfyui_revision": sources["comfyui"]["revision"]}), encoding="utf-8")
        with LOCK:
            STATE.update(status="prepared", phase="Software installed. Download this profile's models, then start ComfyUI and recheck loaded nodes.")
            persist()
    except Exception:
        with LOCK:
            failed_phase = STATE.get("phase", "runtime setup")
            STATE.update(status="failed", phase=f"Setup stopped during: {failed_phase}. Check disk space, Git, network and compatible NVIDIA drivers. Stop the managed runtime before retrying. Personal ComfyUI and credentials were not changed. No models, jobs or rentals were started.")
            persist()


@router.get("")
def status(request: Request):
    require_admin(request)
    with LOCK:
        return {"supported": os.name == "nt", "default_destination": str(get_settings().preferences_file.parent / "local-comfyui"), "operation": read_operation()}


@router.post("/plan")
def review(selection: RuntimeSelection, request: Request):
    require_admin(request, mutation=True)
    try:
        return plan(selection)
    except (ValueError, OSError):
        raise HTTPException(400, "Choose an empty absolute folder, or an existing Commander-managed runtime, and a base profile. Personal ComfyUI installations cannot be overwritten.") from None


@router.post("/apply", status_code=202)
def apply(selection: RuntimeSelection, request: Request):
    require_admin(request, mutation=True)
    if os.name != "nt":
        raise HTTPException(400, "Managed local runtime setup currently supports Windows only.")
    with LOCK:
        if read_operation()["status"] == "preparing":
            raise HTTPException(409, "Runtime setup is already in progress.")
        try:
            data = plan(selection)
        except (ValueError, OSError):
            raise HTTPException(400, "Recheck the installation destination and profile.") from None
        if selection.confirmation != data["confirmation"]:
            raise HTTPException(409, "Review the current installation plan before confirming.")
        parent = Path(data["destination"])
        while not parent.exists():
            parent = parent.parent
        if shutil.disk_usage(parent).free < 20 * 1024**3:
            raise HTTPException(400, "Reserve at least 20 GB for runtime software, plus model space.")
        STATE.clear()
        STATE.update(status="preparing", phase="Starting the reviewed setup", destination=data["destination"], model_set=selection.model_set)
        persist()
        # The installer owns a process lock and outlives a web-only restart.
        # Its operation record also detects an interrupted Windows reboot.
        log = operation_file().with_suffix(".log")
        try:
            with log.open("ab") as output:
                process = subprocess.Popen([sys.executable, "-m", "backend.local_runtime_install"],
                                           cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=output,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            STATE["pid"] = process.pid
            persist()
        except OSError:
            STATE.update(status="failed", phase="Could not start the isolated runtime installer.")
            persist()
            raise HTTPException(500, "Could not start the runtime installer.") from None
        return dict(STATE)
