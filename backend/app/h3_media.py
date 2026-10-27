"""Small, runtime-independent Pocket H3 media manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def h3_input_paths(graph: dict[str, dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for node in graph.values():
        inputs = node.get("inputs", {})
        if node.get("class_type") == "MiniMaxH3ReferencePack":
            try:
                references = json.loads(inputs["references_json"])["references"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid H3 references") from exc
            if not isinstance(references, list) or len(references) > 15:
                raise ValueError("Invalid reference count")
            candidates = [ref.get("file") if isinstance(ref, dict) else None for ref in references]
        elif node.get('class_type') == 'TMPrepareReference':
            candidates = [inputs.get('source_file')]
            candidates += [inputs[key] for key in ('cached_isolation', 'cached_depth') if inputs.get(key)]
        elif node.get("class_type") == "LoadImage":
            candidates = [inputs.get("image")]
        elif node.get('class_type') == 'LoadAudio':
            candidates = [inputs.get('audio')]
        elif node.get("class_type") == "VHS_LoadVideo":
            candidates = [inputs.get("video")]
        else:
            candidates = []
        for path in candidates:
            if (not isinstance(path, str) or not path.startswith("h3_mobile/")
                    or "\\" in path or ".." in Path(path).parts or not Path(path).name):
                raise ValueError("Only Pocket-uploaded references are supported")
            paths.add(path)
    return paths
