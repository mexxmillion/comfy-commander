# Person and scene replacement experiment

This branch adds experimental Motion control inputs: a character image, an
optional location image and one source video. Generation instructions are
automatic; the generation-prompt/AI panel is hidden in this mode. No location
uses targeted SAM3 person replacement. Adding a location selects ordinary H3
reference editing with character as Picture 1 and location as Picture 2.
Scene editing does not use the person-only SAM3 mask or promise protection of
other people. The target tracking description remains available for person-only
replacement; it is a target selector, not a free generation prompt.

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

A second test described a moonlit castle courtyard using only text, with the
same character image and source video, at three seconds/four steps/0.5 MP.
It completed in about 56 seconds and passed full decoding. The background
changed, but sampled output favored the original source performer rather than
the supplied character and gestures drifted. This demonstrates a scene-change
capability, not reliable combined identity/motion control. The final UI does
not expose this prompt-only experiment: it requires image inputs instead.
The final automatic-instruction UI has not had a separate end-to-end render.

Creative Engine previously disabled all mode buttons. Frames and Motion control
now remain selectable and turn Creative Engine off automatically, preserving
its brief. Browser checks verified both switches without submitting a render.

Character Swap v1.5 can be enabled for either replacement route. Advanced
contains its strength control (0–2, default 1), which feeds the LoRA node and
is saved/restored with the recipe. The toggle remains off by default. Enabling
it still requires the Ref2V Community model and available LoRA weights. The
combined scene route with this LoRA needs separate rendering/QC tests.

AI enhancement and Creative Engine use compact toggle rows rather than cards.
The main prompt stays in place and supplies the Creative brief. Extra controls
expand below the toggle; saved batches are visible only while Creative Engine
is enabled. Browser measurements confirmed identical prompt/toggle positions
with Creative Engine off and on.

The branch also adds a 12 px safe-area-aware inset to the Generate header and
draft-status text. Computed browser styles confirmed both left/right insets.
