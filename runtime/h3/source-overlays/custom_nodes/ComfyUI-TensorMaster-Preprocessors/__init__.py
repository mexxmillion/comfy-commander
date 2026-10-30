# MIT licensed; see LICENSE. Only supported reference-processing nodes are exported.
from .nodes import TMIsolateReference, TMCompositeReference
from .prepare import TMDepthReference, TMPrepareReference
from .video import TMPrepareVideo

NODE_CLASS_MAPPINGS = {"TMPrepareVideo": TMPrepareVideo, "TMIsolateReference": TMIsolateReference, "TMCompositeReference": TMCompositeReference,
                       "TMDepthReference": TMDepthReference, "TMPrepareReference": TMPrepareReference}
NODE_DISPLAY_NAME_MAPPINGS = {"TMPrepareVideo": "Prepare video · target size / 24 fps", "TMIsolateReference": "Isolate reference", "TMCompositeReference": "RGB reference background",
                              "TMDepthReference": "Depth Anything V2 Small", "TMPrepareReference": "Prepare reference (cached)"}
