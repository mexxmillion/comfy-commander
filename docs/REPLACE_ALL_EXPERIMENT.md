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
The later automatic-enhancement UI test is described below.

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

## Automatic enhancement and optional direction

Motion control defaults to AI enhancement, with an explicit **AI enhance motion
prompt** toggle. When on, it calls the configured controller writer before
submitting a render. Person-only uses the replacement system prompt; person plus
location uses a separate system prompt that binds Picture 1 to identity/outfit,
Picture 2 to the location and Video 1 only to motion/framing. The controller
constructs canned direction rather than reading an unrelated hidden prompt.
Enhancement failure stops submission; there is no silent unenhanced fallback.
When enhancement is on, the provider must support the supplied media. Turn it
off to render directly with canned or optional custom instructions: no AI
provider/key is required and no enhancement request is made. Ordinary References
rendering can also run without AI. The choice is saved and restored per recipe.

**Optional prompt** is off by default. Turning it on prefills the appropriate
instructions for editing; the controller appends that explicit operator direction
to its canonical motion contract before enhancement. Turning it off uses the
canonical instructions. The final enhanced prompt is retained in the recipe
and reaches the reference pack; the worker does not call the AI provider again.

An actual UI run using the operator's character, city location and mocap video
completed at three seconds/four steps/0.5 MP, with Character Swap enabled at 1.0.
No generation prompt was typed. The saved recipe confirmed enhancement enabled,
and the reference-pack direction matched the AI output. Local generation took
about 86 seconds; the output was 544 × 960, 24 fps, approximately 3.04 seconds,
with AAC audio and successful full decoding. Sampled frames showed the intended
woman/outfit, city setting and pointing gesture. This is not proof of exact
identity, all-frame motion fidelity, soundtrack acceptance or multi-person QC.
Both enhancement and the identity LoRA were enabled, so this is not a controlled
comparison attributing quality gains to either one alone.

Rental failures and ended rentals are now shown together in one collapsed
**Rental history & logs** section. Status labels, diagnostics and uncertain-
creation warnings are retained; history is not automatically expanded.

The branch also adds a 12 px safe-area-aware inset to the Generate header and
draft-status text. Computed browser styles confirmed both left/right insets.
