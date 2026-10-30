# Paused local QwenVL experiment

This branch is unfinished and must not be merged or presented as supported.
The user paused it to prioritize cloud users and GPU rentals.

It sketches a local ComfyUI-QwenVL provider with a fixed text/one-image graph,
node metadata validation and unload-after-inference behavior. Existing LLM
routing tests passed before the later UI edits. The installed node interface
was inspected read-only. No inference, model download, settings/credential
change or server restart was performed.

Before resuming, add regression tests for submission reconciliation, media and
schema limits, queue ownership and dispatch/unload ordering. Test real inference,
JSON reliability and model cleanup. Review installation/version compatibility
and avoid claiming that an upstream model dropdown proves weights are cached.

Main retains OpenRouter and the existing compatible-endpoint integration.
