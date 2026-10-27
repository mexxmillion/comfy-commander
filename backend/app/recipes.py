"""Keep operator intent separate from the text used by the renderer."""


def normalize_recipe(recipe):
    result = dict(recipe or {})
    creative = result.get("creative") or {}
    if creative and "brief" in creative:
        legacy = "sourceDirection" not in result or result.get("direction") != result["sourceDirection"]
        original = creative["brief"] if legacy else result["sourceDirection"]
        final = result.get("aiPrompt") or result.get("direction", "")
        # Early Creative snapshots inherited an empty/stale AI preview from the form.
        if legacy:
            final = result.get("direction", "")
        result.update(direction=original, sourceDirection=original, aiPrompt=final,
                      enhancePrompt=False if legacy else result.get("enhancePrompt", False))
    elif "sourceDirection" in result:
        result["direction"] = result["sourceDirection"]
    return result
