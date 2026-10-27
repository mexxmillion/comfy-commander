"""Recover saved gallery prompts. Dry-run by default; --apply makes a DB backup first.

Run from the repository root: python -m tools.repair_gallery_recipes [--apply]
Only saved recipes and Creative batch briefs supply human text; never infer it
from the executed graph or from another job's prompt.
"""
import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.app.recipes import normalize_recipe


def final_prompt(graph):
    if graph.get("4", {}).get("class_type") == "TextEncodeQwenImage21":
        return graph["4"].get("inputs", {}).get("prompt")
    literal = graph.get("131", {}).get("inputs", {}).get("prompt")
    if isinstance(literal, str):
        return literal
    pack = graph.get("181", {}).get("inputs", {})
    if pack.get("prompt_provider") == "none":
        return pack.get("direction")
    return None


def plan_repairs(db, pocket):
    saved = {job: json.loads(raw) for job, raw in db.execute("SELECT job_id, recipe_json FROM job_recipes")}
    historic = {}
    for raw, in db.execute("SELECT data_json FROM creative_batches"):
        batch = json.loads(raw)
        for concept in batch.get("concepts", []):
            if concept.get("job_id") and concept.get("recipe"):
                candidate = dict(concept["recipe"])
                candidate["creative"] = dict(candidate.get("creative") or {}, brief=batch["brief"])
                historic[concept["job_id"]] = candidate
    changes = {}
    missing = 0
    for job, graph_raw, prompt_id in db.execute(
        "SELECT j.id, j.graph_json, t.prompt_id FROM jobs j JOIN tasks t ON t.job_id=j.id"
    ):
        original = saved.get(job)
        candidate = original or historic.get(job) or pocket.get(prompt_id, {}).get("recipe")
        if not candidate:
            missing += 1
            continue
        recipe = normalize_recipe(candidate)
        batch_recipe = historic.get(job)
        if (batch_recipe and batch_recipe.get("direction") != batch_recipe.get("sourceDirection")
                and recipe.get("sourceDirection") == batch_recipe.get("sourceDirection")
                and recipe.get("aiPrompt") == final_prompt(json.loads(graph_raw))):
            # Repair an inherited form preview only while it still matches the
            # archived batch snapshot; preserve human edits made after that batch.
            source = batch_recipe["creative"]["brief"]
            recipe.update(direction=source, sourceDirection=source)
        if recipe.get("mode") in {"h3", "qwen"}:
            source = recipe.get("sourceDirection", recipe.get("direction", ""))
            recipe.update(direction=source, sourceDirection=source)
            final = final_prompt(json.loads(graph_raw))
            if isinstance(final, str) and final != source:
                recipe["aiPrompt"] = final
        if recipe != original:
            changes[job] = recipe
    return changes, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("state/dispatcher.sqlite3"))
    parser.add_argument("--pocket-recipes", type=Path,
                        default=Path("X:/git/ComfyUI/user/pocket_studio_recipes.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    pocket = json.loads(args.pocket_recipes.read_text(encoding="utf-8")) if args.pocket_recipes.exists() else {}
    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode={'rw' if args.apply else 'ro'}", uri=True) as db:
        changes, missing = plan_repairs(db, pocket)
        report = {"repairs": len(changes), "jobs_without_saved_recipe": missing, "applied": args.apply}
        if args.apply and changes:
            backup = args.database.with_name(args.database.name + "." + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".backup")
            with sqlite3.connect(backup) as target:
                db.backup(target)
            report["backup"] = str(backup.resolve())
            db.executemany("INSERT INTO job_recipes(job_id,recipe_json) VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET recipe_json=excluded.recipe_json",
                           [(job, json.dumps(recipe)) for job, recipe in changes.items()])
        print(json.dumps(report))


if __name__ == "__main__":
    main()
