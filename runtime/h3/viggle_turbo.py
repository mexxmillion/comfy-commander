"""Qwen Image 2.1 Viggle Turbo schedule and lossless runtime LoRA nodes."""

import json
import math

import torch
import torch.nn.functional as F

import comfy.patcher_extension
import comfy.utils
import folder_paths


class ViggleTurboSigmas:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT",),
            "nodes": ("STRING", {"default": "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25"}),
        }}

    RETURN_TYPES = ("SIGMAS",)
    FUNCTION = "get_sigmas"
    CATEGORY = "sampling/custom_sampling/schedulers"

    def get_sigmas(self, latent, nodes):
        samples = latent["samples"]
        ratio = latent.get("downscale_ratio_spacial", 16) / 16
        tokens = round(samples.shape[-2] * ratio) * round(samples.shape[-1] * ratio)
        mu = 0.5 + (0.9 - 0.5) * (tokens - 256) / (8192 - 256)
        timesteps = torch.tensor([float(value) for value in nodes.split(",")], dtype=torch.float64)
        sigmas = math.exp(mu) / (math.exp(mu) + (1 / timesteps - 1))
        return (torch.cat([sigmas, sigmas.new_zeros(1)]).float(),)


def lora_forward(value, weights):
    return F.linear(F.linear(value, weights[0].to(value.dtype)), weights[1].to(value.dtype))


def add_hook(module, weights):
    return module.register_forward_hook(lambda _module, inputs, output: output + lora_forward(inputs[0], weights))


def add_mlp_hooks(mlp, gate, up, down):
    state = {}

    def gate_up_hook(_module, inputs, output):
        state["gate_up"] = output + torch.cat([lora_forward(inputs[0], gate), lora_forward(inputs[0], up)], -1)
        return state["gate_up"]

    def mlp_hook(_module, _inputs, output):
        gate_value, up_value = state.pop("gate_up").chunk(2, -1)
        return output + lora_forward(F.silu(gate_value) * up_value, down)

    return [mlp.gate_up.register_forward_hook(gate_up_hook), mlp.register_forward_hook(mlp_hook)]


def run_with_lora(lora, executor, *args, **kwargs):
    diffusion_model = executor.class_obj
    for weights in lora.values():
        if weights[0].device != args[0].device:
            weights[0], weights[1] = weights[0].to(args[0].device), weights[1].to(args[0].device)
    hooks = []
    for name, weights in lora.items():
        parent, _, leaf = name.rpartition(".")
        if not getattr(diffusion_model.get_submodule(parent), "fused", False):
            hooks.append(add_hook(diffusion_model.get_submodule(name), weights))
        elif leaf == "out":
            hooks += add_mlp_hooks(diffusion_model.get_submodule(parent), lora[parent + ".gate_layer"], lora[parent + ".proj"], weights)
    try:
        return executor(*args, **kwargs)
    finally:
        for hook in hooks:
            hook.remove()


class ViggleTurboLora:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "lora_name": (folder_paths.get_filename_list("loras"),),
            "strength": ("FLOAT", {"default": 1.0, "min": -4.0, "max": 4.0, "step": 0.05}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load"
    CATEGORY = "loaders"

    def load(self, model, lora_name, strength):
        state_dict, metadata = comfy.utils.load_torch_file(folder_paths.get_full_path_or_raise("loras", lora_name), return_metadata=True)
        config = json.loads((metadata or {}).get("lora_adapter_metadata", "{}"))
        scale = strength * config.get("transformer.lora_alpha", 1) / config.get("transformer.r", 1)
        lora = {
            key.removeprefix("transformer.").removesuffix(".lora_A.weight"): [state_dict[key], state_dict[key.replace("lora_A", "lora_B")] * scale]
            for key in state_dict if key.endswith(".lora_A.weight")
        }
        patched = model.clone()
        patched.add_wrapper_with_key(
            comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL,
            "viggle_turbo_lora",
            lambda executor, *args, **kwargs: run_with_lora(lora, executor, *args, **kwargs),
        )
        return (patched,)


NODE_CLASS_MAPPINGS = {"ViggleTurboSigmas": ViggleTurboSigmas, "ViggleTurboLora": ViggleTurboLora}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ViggleTurboSigmas": "Qwen Image 2.1 Viggle Turbo Sigmas",
    "ViggleTurboLora": "Qwen Image 2.1 Viggle Turbo LoRA (unmerged)",
}
