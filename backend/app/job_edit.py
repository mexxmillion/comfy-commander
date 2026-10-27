"""Recover Pocket settings without modifying or resubmitting the source job."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .creative import CreativeBatchRow
from .store import JobRow, JobRecipeRow
from .recipes import normalize_recipe


def edit_setup(store, job_id, input_root):
    warnings = []
    with Session(store.engine) as db:
        job = db.get(JobRow, job_id)
        if job is None:
            return {"recipe": None, "warnings": ["Job no longer exists."]}
        graph = json.loads(job.graph_json)
        saved = db.get(JobRecipeRow, job_id)
        recipe = json.loads(saved.recipe_json) if saved else None
        if recipe and recipe.get('mode') == 'reference-isolate':
            return {'recipe': None, 'warnings': ['Open an image reference’s Isolate editor to reuse this preprocessing result.']}
        if recipe is None:
            for row in db.scalars(select(CreativeBatchRow)):
                for concept in json.loads(row.data_json).get("concepts", []):
                    if concept.get("job_id") == job_id:
                        recipe = concept.get("recipe")
                        break
                if recipe is not None:
                    break
        if recipe is not None:
            recipe = normalize_recipe(recipe)
        if recipe is not None and recipe.get('mode') == 'ltx':
            root=input_root.resolve()
            source=(root/recipe.get('sourceFile','')).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                warnings.append('Source video is missing; upload it again before rendering.')
            recipe['renderTarget']='tensor-master-any' if job.policy == 'any' else 'tensor-master-cloud' if job.policy == 'standard' else 'tensor-master'
            return {'recipe':recipe,'warnings':warnings}
        if recipe is not None and recipe.get('mode') == 'krea':
            recipe['renderTarget']='tensor-master-any' if job.policy == 'any' else 'tensor-master-cloud' if job.policy == 'standard' else 'tensor-master'
            root = input_root.resolve()
            for ref in recipe.get('references', []):
                path = (root / ref.get('file', '')).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    warnings.append('Missing reference: ' + ref.get('file', '(unknown)'))
            return {'recipe': recipe, 'warnings': warnings}
        if recipe is not None and recipe.get("mode") in {"faces", "voice"}:
            recipe["renderTarget"] = "tensor-master-any" if job.policy == "any" else "tensor-master-cloud" if job.policy == "standard" else "tensor-master"
            root = input_root.resolve()
            for ref in recipe.get("references", []):
                path = (root / ref.get("file", "")).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    warnings.append(f"Missing reference: {ref.get('file', '(unknown)')}. Replace it before running.")
            return {"recipe": recipe, "warnings": warnings}
        def inputs(node):
            return graph.get(node, {}).get("inputs", {})
        qwen = graph.get("4", {}).get("class_type") == "TextEncodeQwenImage21"
        if qwen and recipe is None:
            turbo = graph.get("10", {}).get("class_type") == "ViggleTurboLora"
            refs = [{"kind": "image", "file": node["inputs"]["image"]}
                    for key, node in sorted(graph.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 9999)
                    if node.get("class_type") == "LoadImage" and node.get("inputs", {}).get("image")]
            recipe = dict(mode="qwen", direction="", sourceDirection="",
                          aiPrompt=inputs("4").get("prompt", ""), enhancePrompt=False,
                          negative=inputs("4").get("negative_prompt", ""),
                          references=refs, turbo=turbo, steps=6 if turbo else inputs("9").get("steps", 25),
                          cfg=1 if turbo else inputs("9").get("cfg", 1),
                          seed=inputs("11").get("noise_seed") if turbo else inputs("9").get("seed"),
                          referenceResolution=inputs("4").get("resolution", 1024),
                          cache="off" if inputs("6").get("device") == "off" else inputs("6").get("dtype", "default"),
                          loras=[{"name": node["inputs"]["lora_name"], "strength": node["inputs"]["strength_model"]}
                                 for node in graph.values() if node.get("class_type") == "LoraLoaderModelOnly"])
            warnings.append("Recovered Qwen settings from the workflow. Review output size before running.")
            warnings.append("Original human prompt was not saved; final render text restored to the AI field.")
        if recipe is not None and recipe.get("mode") == "qwen":
            recipe["renderTarget"] = "tensor-master-any" if job.policy == "any" else "tensor-master-cloud" if job.policy == "standard" else "tensor-master"
            root = input_root.resolve()
            for ref in recipe.get("references", []):
                path = (root / ref.get("file", "")).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    warnings.append(f"Missing reference: {ref.get('file', '(unknown)')}. Replace it before running.")
            return {"recipe": recipe, "warnings": warnings}
        frame = graph.get("131", {}).get("class_type") == "MiniMaxH3ImageToVideo"
        if graph.get("131", {}).get("class_type") not in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}:
            return {"recipe": None, "warnings": ["This workflow cannot be edited in Pocket H3."]}
        if recipe is None:
            refs = []
            if frame:
                for kind, node in [("first_frame", "183"), ("last_frame", "184")]:
                    if inputs(node).get("image"):
                        refs.append({"kind": kind, "file": inputs(node)["image"]})
            else:
                refs = json.loads(inputs("181").get("references_json", "{}")).get("references", [])
            director = inputs("185" if frame else "181")
            recipe = dict(mode="h3", h3Type="frames" if frame else "reference", references=refs,
                          direction=director.get("prompt" if frame else "direction", ""),
                          baseModel=inputs("127").get("unet_name"), steps=inputs("124").get("steps"),
                          megapixels=inputs("162").get("megapixels"), aspect=inputs("162").get("aspect_ratio"),
                          duration=inputs("133").get("value"), seed=inputs("129").get("noise_seed"),
                          turboEnabled="145" in graph,
                          turbo=4 if "4step" in inputs("145").get("lora_name", "") or inputs("124").get("steps", 4)<=5 else 8,
                          enhancePrompt=director.get("prompt_provider")=="openrouter",
                          aiModel=director.get("openrouter_model"), jobType=director.get("job_type", "standard"),
                          loras=[{"name": n["inputs"]["lora_name"], "strength": n["inputs"]["strength_model"]}
                                 for key,n in graph.items() if key.isdigit() and int(key)>=200 and n.get("class_type")=="LoraLoaderModelOnly"])
            warnings.append("Recovered from the older workflow. Review settings before running.")
            if director.get("prompt_provider") != "openrouter":
                recipe.update(direction="", sourceDirection="", enhancePrompt=False)
                warnings.append("Original human prompt was not saved; final render text restored to the AI field.")
        # Older frame-mode snapshots incorrectly always recorded Turbo enabled.
        recipe["turboEnabled"] = "145" in graph
        recipe["turboStrength"] = inputs("145").get("strength_model", 0.75)
        literal = inputs("131").get("prompt")
        if not isinstance(literal, str) and not frame and inputs("181").get("prompt_provider") == "none":
            # Pocket pre-writes the AI prompt and passes it through the reference pack verbatim.
            literal = inputs("181").get("direction")
        if isinstance(literal, str):
            original = recipe.get("sourceDirection", recipe.get("direction", ""))
            recipe.update(direction=original, sourceDirection=original,
                          aiPrompt=literal if literal != original else recipe.get("aiPrompt", ""))
        elif recipe.get("enhancePrompt"):
            warnings.append("Final AI-rewritten text was not saved. Original brief restored; AI enhancement will run again unless disabled.")
        recipe["renderTarget"] = "tensor-master-any" if job.policy == "any" else "tensor-master-cloud" if job.policy == "standard" else "tensor-master"
        for key in ["baseModel", "steps", "megapixels", "aspect", "duration", "seed"]:
            if recipe.get(key) is None:
                return {"recipe": None, "warnings": [f"Cannot safely restore this older job: missing {key}."]}
        root = input_root.resolve()
        for ref in recipe.get("references", []):
            path = (root / ref.get("file", "")).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                warnings.append(f"Missing reference: {ref.get('file', '(unknown)')}. Replace it before running.")
        return {"recipe": recipe, "warnings": warnings}
