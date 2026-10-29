# MIT licensed; see LICENSE. Only supported reference-processing nodes are exported.
from .nodes import TMIsolateReference, TMCompositeReference
from .prepare import TMDepthReference, TMPrepareReference

NODE_CLASS_MAPPINGS = {"TMIsolateReference": TMIsolateReference, "TMCompositeReference": TMCompositeReference,
                       "TMDepthReference": TMDepthReference, "TMPrepareReference": TMPrepareReference}
NODE_DISPLAY_NAME_MAPPINGS = {"TMIsolateReference": "Isolate reference", "TMCompositeReference": "RGB reference background",
                              "TMDepthReference": "Depth Anything V2 Small", "TMPrepareReference": "Prepare reference (cached)"}
