"""Text-grounded image cutouts. Original integration; upstream models stay separate."""
from pathlib import Path

import numpy as np
import torch
from PIL import Image

VERSION = "reference-isolate-v1"


def select_boxes(boxes, scores, selection):
    """Stable reading order makes numbered subjects repeatable between previews."""
    order = sorted(range(len(boxes)), key=lambda i: (float(boxes[i][0]), float(boxes[i][1])))
    if not order:
        raise ValueError("No matching subject found. Try 'shirt', 'jacket', or 'person', or lower confidence.")
    if selection == "all":
        return order
    if selection == "best":
        return [max(order, key=lambda i: float(scores[i]))]
    index = int(selection) - 1
    if not 0 <= index < len(order):
        raise ValueError(f"Only {len(order)} matching subjects found; choose All, Best, or a lower subject number.")
    return [order[index]]


class TMIsolateReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "text": ("STRING", {"default": "person", "multiline": False}),
            "confidence": ("FLOAT", {"default": 0.25, "min": 0.05, "max": 0.95, "step": 0.05}),
            "selection": (["all", "best"] + [str(i) for i in range(1, 21)],),
        }}

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("cutout_rgba", "foreground_mask")
    FUNCTION = "isolate"
    CATEGORY = "Tensor Master/Reference preprocessing"

    @torch.inference_mode()
    def isolate(self, image, text, confidence=0.25, selection="all"):
        import comfy.model_management as mm
        import folder_paths
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

        text = text.strip().lower()
        if not text or len(text) > 200:
            raise ValueError("Describe the subject to isolate in 1–200 characters.")
        if image.shape[0] != 1:
            raise ValueError("Isolate reference currently accepts one still image at a time.")
        if image.shape[1] * image.shape[2] > 20_000_000:
            raise ValueError("Resize the reference below 20 megapixels before isolating it.")
        root = Path(folder_paths.models_dir) / "tensor-master-preprocessors"
        dino_path, sam_path = root / "grounding-dino-tiny", root / "sam2.1-hiera-tiny"
        for path in (dino_path, sam_path):
            if not (path / "model.safetensors").is_file():
                raise RuntimeError("Preprocessing weights missing. Run the worker profile model-sync step first.")
        # Participate in Comfy's memory scheduling before creating temporary models.
        mm.free_memory(2 * 1024**3, mm.get_torch_device())
        device = mm.get_torch_device()
        source = Image.fromarray((image[0, :, :, :3].cpu().numpy().clip(0, 1) * 255).round().astype(np.uint8))
        detector = segmenter = inputs = output = sam_inputs = prediction = None
        try:
            processor = AutoProcessor.from_pretrained(dino_path, local_files_only=True)
            detector = AutoModelForZeroShotObjectDetection.from_pretrained(dino_path, local_files_only=True,
                                                                          use_safetensors=True).to(device).eval()
            inputs = processor(images=source, text=text if text.endswith(".") else text + ".", return_tensors="pt").to(device)
            output = detector(**inputs)
            detected = processor.post_process_grounded_object_detection(output, inputs.input_ids,
                threshold=float(confidence), text_threshold=0.2, target_sizes=[(source.height, source.width)])[0]
            boxes = detected["boxes"].detach().cpu()
            scores = detected["scores"].detach().cpu()
            chosen = select_boxes(boxes, scores, selection)
            if len(chosen) > 20:
                raise ValueError("More than 20 matches. Use a more specific description or select Best.")
            # Release detector before allocating SAM; no permanent GPU model cache.
            output = inputs = None
            detector.cpu()
            detector = None
            mm.soft_empty_cache()

            sam_processor = Sam2Processor.from_pretrained(sam_path, local_files_only=True)
            segmenter = Sam2Model.from_pretrained(sam_path, local_files_only=True,
                                                use_safetensors=True).to(device).eval()
            sam_inputs = sam_processor(images=source, input_boxes=[boxes[chosen].tolist()], return_tensors="pt").to(device)
            prediction = segmenter(**sam_inputs, multimask_output=False)
            masks = sam_processor.post_process_masks(prediction.pred_masks.cpu(), sam_inputs["original_sizes"].cpu())[0]
            mask = masks.reshape(-1, source.height, source.width).any(dim=0).float()
            if not mask.any():
                raise ValueError("The selected subject produced an empty mask. Try another description.")
            foreground = image[0, :, :, :3].cpu() * mask.unsqueeze(-1)
            rgba = torch.cat((foreground, mask.unsqueeze(-1)), dim=-1).unsqueeze(0)
            return (rgba, mask.unsqueeze(0))
        finally:
            inputs = output = sam_inputs = prediction = None
            if detector is not None:
                detector.cpu()
            if segmenter is not None:
                segmenter.cpu()
            mm.soft_empty_cache()

class TMCompositeReference:
    """Composite the isolated RGBA image into the exact RGB input used downstream."""
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',),
                'background': (['gray', 'white', 'black'],),
                'crop': ('BOOLEAN', {'default': True})}}

    RETURN_TYPES = ('IMAGE',)
    RETURN_NAMES = ('rgb_reference',)
    FUNCTION = 'composite'
    CATEGORY = 'Tensor Master/Reference preprocessing'

    def composite(self, image, background='gray', crop=True):
        if image.shape[0] != 1 or image.shape[-1] != 4:
            raise ValueError('Composite reference requires one RGBA isolation result.')
        alpha = image[..., 3:4].clamp(0, 1)
        if not (alpha > 0).any():
            raise ValueError('Cannot composite an empty foreground mask.')
        if crop:
            positions = torch.nonzero(alpha[0, :, :, 0] > 0, as_tuple=False)
            top, left = positions.min(dim=0).values.tolist()
            bottom, right = (positions.max(dim=0).values + 1).tolist()
            pad = max(4, round(max(right - left, bottom - top) * 0.05))
            image = image[:, max(0, top-pad):min(image.shape[1], bottom+pad),
                          max(0, left-pad):min(image.shape[2], right+pad)]
            alpha = image[..., 3:4].clamp(0, 1)
        color = {'gray': 127 / 255, 'white': 1.0, 'black': 0.0}[background]
        return (image[..., :3] * alpha + color * (1 - alpha),)

