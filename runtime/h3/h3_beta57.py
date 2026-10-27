"""Expose the Eros beta(0.5, 0.7) schedule through BasicScheduler."""
from functools import partial

import comfy.samplers

if "beta57" not in comfy.samplers.SCHEDULER_HANDLERS:
    comfy.samplers.SCHEDULER_HANDLERS["beta57"] = comfy.samplers.SchedulerHandler(
        partial(comfy.samplers.beta_scheduler, alpha=0.5, beta=0.7)
    )
    comfy.samplers.SCHEDULER_NAMES.append("beta57")

NODE_CLASS_MAPPINGS = {}
