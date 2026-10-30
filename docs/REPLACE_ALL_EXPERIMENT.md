# Person and scene replacement experiment

This branch contains an initial test report, not a new supported UI mode.
The existing H3 References workflow already accepts two images and one video.

The short local test assigned Picture 1 to character identity/outfit, Picture 2
to a mountain-lake setting and Video 1 to motion/timing. It used the public
Ref2V INT8 model, matching four-step Ref2V Turbo LoRA, 0.5 MP, portrait 9:16,
three requested seconds, audio lock on and sparse attention off. No cloud GPU,
LLM request, new node installation or generation allowlist change was needed.

The original test failed before sampling because the experiment supplied trim
as an object instead of the required `[start_seconds, end_seconds]` list. It was
retained as failed evidence. The corrected test completed on a local RTX 5080
in about 60 seconds: 544 × 960, 24 fps, approximately 3.04 seconds, AAC audio.
Full video/audio decoding passed. Sampled frames showed the replacement identity
and setting, with approximate source gestures. Exact choreography, facial
consistency, continuous playback and soundtrack acceptance still need user QC.

This test used the ordinary H3 reference route, not the existing person-only
SAM3 inversion route. That route intentionally preserves the original background.
No claim is made that a target-only mask supports replacing the whole setting.
Person-only and background-only comparison runs were not performed in this test.
Do not advertise three tested replacement modes based on this single result.

The branch also adds a 12 px safe-area-aware inset to the Generate header and
draft-status text. Computed browser styles confirmed both left/right insets.
