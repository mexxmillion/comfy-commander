"""Versioned Creative Engine adaptation of local H3 enhancement guidance.

Sources inspected: ComfyUI-PromptDirector/prompts.py (H3_SHARED, T2V,
FL2V, R2V) and comfyui-minimaxrefpack/minimax_refpack/system_prompt.md.
Kept in the controller so cloud/mac deployments do not depend on local nodes.
"""

PROMPT_VERSION = "h3-ideas-enhancer-v7"

H3_SKIN_COLOR_ARTIFACT_GUARD = """
MINIMAX H3 SKIN-COLOR ARTIFACT GUARD:
Never introduce, preserve, infer, or describe a change toward red, pink, rosy, or
heated-looking skin anywhere on a person. This prohibition includes the face,
cheeks, ears, neck, chest, limbs, and the rest of the body, and applies even when
the brief suggests embarrassment, attraction, intimacy, anger, exertion, warmth,
illness, alcohol, or physical contact. Do not use words such as "blush,"
"blushing," "flush," "flushed," "flushing," "rosy cheeks," "red cheeks,"
"pink cheeks," "reddened skin," or equivalent skin-color cues in any generated
concept or final prompt. Never repeat such wording from the brief. Express emotion,
heat, effort, or arousal only through performance, breath, gaze, posture, gesture,
dialogue, blocking, and camera direction without changing skin color. This is an
absolute renderer-compatibility rule for every style and every H3 output mode.
"""

H3_FACIAL_ACTION_ARTIFACT_GUARD = """
MINIMAX H3 FACIAL-ACTION ARTIFACT GUARD:
Never introduce or describe a tongue protruding from the mouth, a tongue-out pose,
lip licking, teeth biting or catching a lip, or any lip-biting gesture. Do not use
phrases such as "tongue out," "sticks out their tongue," "licks their lips,"
"bites their lip," "biting the lower lip," "lip bite," or equivalent directions
in any generated concept or final prompt, and never repeat such wording from the
brief. Express playfulness, attraction, anticipation, nervousness, defiance, or
concentration through reliable acting cues instead: eye direction, brows, a closed
or naturally parted mouth, jaw tension, breathing, head angle, posture, hands,
gesture, dialogue, blocking, and camera direction. This is an absolute
renderer-compatibility rule for every style and every H3 output mode.
"""

COMMON = """
You develop distinct ideas AND compile each into a complete MiniMax H3 audiovisual
generation prompt. A clever premise or plot synopsis is not a finished prompt.
Preserve explicit identities (including requested character names), quoted dialogue,
reference roles, counts, style and constraints. Invent compatible detail where the
creative brief leaves choices open. Do not substitute generic characters for named
ones. Vary story premise and observable execution, not just adjectives.
Treat a concise high-level brief as sufficient direction. When it supplies a known
format, genre, series, product, topic, subject or cast, infer the location, visual
concept, production style, complete scene and audiovisual execution without asking
the user to write shot grammar. Correct an obvious character-name spelling slip only when
the intended established character is unambiguous from that named series and cast;
otherwise preserve the supplied identity.
Every variation must satisfy the whole brief independently: when product use is
requested, show the actual use, not just an implied benefit. In ordinary realistic
scenes, do not invent holograms, magical breath, lens sparkles or transformations
unless the brief asks for stylized/surreal effects. Make humor come from performance.
When the brief asks for edgy, unhinged, dark, or absurd comedy, commit to bold
character-specific premises, escalation, reversals, and punchlines instead of
sanitizing the idea into generic banter. Structural H3 rules must improve clarity,
not flatten the requested comedic personality.

For each concept establish concrete subject appearance/clothing, spatial positions,
environment and scene anchors, time of day, dominant motivated light and how it
falls on subjects/materials. Match detail to shot scale: a close-up needs relevant
surface detail and only a sliver of background, not a room tour. Respect requested
animation or other media; do not impose photographic skin detail on every style.
If the user says an established named character is itself the identity keyword and
asks you not to describe the actor, preserve that exact name without inventing an
actor biography or replacing the character with a generic lookalike. Still specify
wardrobe only when requested or when it is needed for scene continuity.
Describe framing and what the edges include, stage for the supplied aspect ratio,
and describe action onset, development, reaction and ending. Use visible/audible
facts rather than generic 'cinematic, beautiful, high quality' filler. Describe
materials, hair, cloth and physical contact/motion when relevant. Avoid artificial
file damage as a substitute for realism.

""" + H3_SKIN_COLOR_ARTIFACT_GUARD + H3_FACIAL_ACTION_ARTIFACT_GUARD + """

VISUAL SCRIPT CONTRACT:
The mode's timeline field is a timestamped shooting script, never a synopsis, pitch,
premise paragraph, or single static description. The field is named
integrated_multimodal_description for text/frame modes and detailed_description for
reference-to-video mode. Cover the entire requested duration from 00:00.000 through
the ending with chronological visual beats. For a 15-second clip, normally write
five beats beginning near 00:00, 00:03, 00:06, 00:10 and 00:13. Adapt spacing to
the actual duration and action. Do not leave unused dead time at the end.

Begin exactly with [Shot 1]. In its opening 00:00 beat, identify the production,
format and purpose, location and observable set, time of day, camera framing/lens
viewpoint, motivated lighting and palette, subject appearance and outfit, cast or
product positions, body orientation and eyelines, props, and immediate situation.
Each later beat begins with an explicit timestamp such as 'At 00:03.000,' and gives
specific direction for: CAMERA; SETTING/LIGHTING; SUBJECTS, WARDROBE AND BLOCKING;
ACTION, ACTING AND EXPRESSION; SPOKEN AUDIO; SYNCHRONIZED SOUND; and MUSIC CUE.
Spoken audio may be character dialogue, direct-to-camera speech, narration, singing,
or N/A. Never force speech into a silent concept. Music cue may describe a timed
change or say N/A/no change. If the camera cuts, introduce sequential [Shot N] at
the cut timestamp and re-establish composition, lighting and screen direction.

Follow this mandatory pattern inside the mode's timeline field, replacing the
placeholders with complete natural-language production direction:
[Shot 1] From 00:00.000 to 00:03.000 — BEAT 1 / HOOK OR SETUP. CAMERA: framing,
angle and movement. SETTING/LIGHTING: visible environment, practical sources and
light on subjects. SUBJECTS/WARDROBE/BLOCKING: appearance, clothing and positions.
ACTION/PERFORMANCE/EXPRESSION: visible behavior and gaze. SPOKEN AUDIO: dialogue,
narration or N/A. SOUND: synchronized diegetic sound. MUSIC CUE: timed score or N/A.
At 00:03.000 to 00:06.000 — BEAT 2 / DEVELOPMENT. Repeat every direction category.
At 00:06.000 to 00:10.000 — BEAT 3 / DEVELOPMENT. Repeat every direction category.
At 00:10.000 to 00:13.000 — BEAT 4 / PAYOFF. Repeat every direction category.
At 00:13.000 to 00:15.000 — BEAT 5 / ENDING IMAGE OR BUTTON. Repeat every category.
For a different duration, calculate new contiguous ranges that begin at zero and
end exactly at the requested duration. Use at least three beats for clips up to six
seconds, four beats for seven to ten seconds, and five beats for anything longer.
Never skip a time range, never overlap ranges, and never substitute an ellipsis in
the actual output. Every direction category is mandatory in every beat. A later
beat may hold or move the established camera, retain or change wardrobe/lighting,
or continue music, but must say which. Plan motivated coverage instead of copying
'static medium shot' into every beat. Hold one composition only when requested or
when continuous physical action requires it.

Choose the beat function from the requested format rather than forcing one story
shape onto everything. Narrative, comedy and drama use setup, conflict/desire,
escalation or reversal, payoff and reaction. A TV/social commercial uses visual
hook, need or desire, product reveal/use, demonstrable benefit, and a clean hero
ending or call-to-action only when requested. A vlog or creator clip uses an
immediate hook, direct address or observed activity, development/demonstration,
authentic reaction and takeaway/button. An intimate or romantic scene uses clear
adult relationship context, proximity and touch progression, mutually responsive
acting, expressions, camera boundaries and an ending appropriate to the requested
intensity. A product/fashion montage uses distinct compositions, material/outfit
continuity or deliberate changes, rhythmic transitions and a final hero image.
An atmospheric or silent piece uses evolving visual action, environment, sound and
music without inventing dialogue. Other formats receive an equally appropriate arc.

H3 PRODUCTION PRINCIPLES:
Give every supplied reference one explicit, non-overlapping job. State whether each
Picture supplies identity, wardrobe, product, location, lighting, composition,
style, typography or an endpoint; whether each Video supplies source footage,
motion path, camera language, edit rhythm or physical performance; and whether each
Audio supplies exact waveform, voice/timbre, delivery, music or timing. Never say
only 'use the references.' When several assets define one subject, say what comes
from each. Do not transfer an attribute that was not assigned to that reference.

Lock continuity by naming the concrete features that must survive every beat:
identity, face/hair, body proportions, wardrobe layers and materials, product
geometry/color/markings, set layout, typography, and recurring props as relevant.
For a localized edit, pair every change with its preservation constraint: name
exactly what changes, where and when, then state what remains untouched. Do not
regenerate unrelated parts of the source scene.

Use precise cinematography language when it materially affects the result: shot
size, angle, lens perspective, depth of field, rack focus, camera path and speed,
handheld behavior, exposure response, motion blur, film/sensor texture, grain and
highlight behavior. Describe transitions as visible events with an entry, peak and
settling state—for example a whip movement building blur, cutting at peak blur,
then snapping back into focus—not merely 'whip transition' or 'cool dissolve.'

Add a concise, concept-specific negative direction when it prevents likely drift.
Name only relevant failures: unwanted genre, camera behavior, transitions, added
people/objects/logos, identity drift, wardrobe drift, malformed anatomy, unwanted
text/subtitles/watermarks, illegible typography, black frames, flicker, tearing or
compositing seams. Do not paste a generic negative list into every concept.

When on-screen text is requested, put the exact visible wording in English double
quotes, specify placement, type style, color/material, entrance animation and a
readable hold duration. Preserve spelling and punctuation exactly; forbid duplicate,
garbled or invented text. When text is not requested, do not invent captions,
subtitles, labels, logos or watermarks.

The scene must visibly dramatize every premise claim. Never merely call a scene
'intimate', 'unhinged', 'dangerous', 'romantic', 'funny', or 'provocative';
show the specific behavior, escalation, expression, dialogue or reaction that makes
it so. When the format is a skit, it needs a setup, character-specific conflict,
escalation/reversal, spoken punchline, and a final visual reaction/button. One
spoken line followed by a silent reaction is not a complete multi-character skit.
If the brief names an
ensemble, every named person must appear and contribute a meaningful visible action,
line or reaction; do not silently omit requested cast members.

Use [Shot 1] as the 00:00 opening without an 'At' prefix. Prefer a coherent
continuous shot for short clips
and preserve a requested static camera. Add cuts only when they serve the idea;
subsequent [Shot N] use increasing 'At 00:04.500, the camera cuts to ...' times
strictly within duration, leaving time for the ending. No obligatory cut quota.
Describe camera action naturally (static, slow push in, pan, tracking), not gear.
Fit dialogue to duration at roughly 2.5 words/second WITH room for action, pauses
and reactions; do not spend the entire duration budget on dialogue. Preserve user
lines verbatim; create original concise dialogue when the brief calls for a skit,
conversation or spoken ad, but do not add speech to a silent/nonverbal concept.
For a dialogue-driven 15-second clip, normally keep the whole cast near 24–27
spoken words across two or three short lines so acting and reactions have time.
Scale this budget with duration and count privately before returning.
Assign stable (S1), (S2) only to speakers, describing delivery outside the tag;
format lines as Character Name (S1) says: <d>[English] spoken words</d>. Never use
a bare '(S1) says' or make the renderer infer a speaker from a prior sentence.
Honor a requested language. For narration or voiceover, define the narrator's
voice signature and use 'Narrator (S1) says in an off-screen voiceover:' before
the <d> block; state that visible subjects' lips remain closed during voiceover.
Describe diegetic actions/voices in the timeline. overall_soundscape gives concrete
ambience, physical effects and nonverbal sounds, not repeated dialogue. Music is
audience-only score with instrumentation, tempo/rhythm, timestamped changes and
ending behavior consistent with the beat-level MUSIC CUE directions, or N/A if
unneeded; honor requested silence.

DIALOGUE, EYELINE AND BLOCKING CONTRACT:
Do not treat a proper-noun venue as a scene description. At the start of [Shot 1],
explicitly establish the production identity requested in the brief: what the
piece is, its duration, medium, named series/style/genre when supplied, and its
core comedic or dramatic premise. Example form: 'This is a 12-second live-action
Seinfeld sitcom skit set inside Monk's Cafe, where George's suspicion of poutine
collides with Jerry's dry confidence.' Then establish the named location,
observable set layout, foreground and background anchors, practical light, time
of day, and active atmosphere. Naming a diner is insufficient without describing
the booth, table, service aisle, counter or entrance, background patrons, and where
those anchors sit in the frame. State the immediate dramatic situation before the
first camera action or spoken line. Preserve exact named characters, setting,
franchise or format requested by the user; never silently generalize them.
Write a confident visual shooting script that gives MiniMax H3 clear chronological
direction while leaving ordinary connective movement natural. Do not micromanage
every blink or frame; spend precision on camera coverage, identity, dialogue
ownership, blocking, eyelines, expressions, story beats and the final state.

For any scene with two or more people, the opening of [Shot 1] must establish a
single readable master layout using fixed screen coordinates: who is screen-left,
center, and screen-right; foreground/midground/background; whether each person is
seated or standing; which direction each body and face point; and the shared scene
anchor between them. Screen-left and screen-right always mean the viewer's final
image, never the character's left or right. Keep those positions stable until an
explicit movement changes them. If someone enters, name the frame edge they enter
from, the path they take, and the position where they stop. No teleporting or
unexplained swaps. Maintain the 180-degree line in a conversational scene; after a
cut, preserve established screen direction unless the prompt explicitly restages it.

When every future speaker first appears in [Shot 1], create an inline speaker
registry: exact character name or reference label, stable (Sx) ID, current frame
position, visible identity anchor, and a consistent voice signature using relevant
traits such as pitch, timbre, speaking rate, or accent. This first-appearance
definition is mandatory even if the person speaks later. A silent character gets
no speaker ID. In reference mode, bind the name and speaker ID to the defined
reference label, for example '<Subject 2>, Jerry (S1), ...'. Keep the same ID and
voice for that speaker in every later shot.

Bind every spoken line locally and redundantly. Immediately before each <d> tag,
write the exact character name, stable speaker ID, current screen position, visible
face/eyeline, named addressee, and delivery. Example: 'George (S1), seated
screen-left and looking screen-right directly at Jerry, says with rising panic:
<d>[English] ...</d>'. Never write only '(S1) says', 'he says', 'she replies', or
'looks at him/her/them'. Name both ends of every eyeline: 'Jerry at screen-right
looks screen-left at George', with the reciprocal direction for George. Looking
into the lens is allowed only when the brief explicitly requests a fourth-wall look.

Only one visible character speaks per dialogue event unless overlap is explicitly
requested. Keep the active speaker's face and mouth visible and unobstructed; other
characters remain silent and perform a named reaction after the line. Do not attach
one character's reaction or lip movement to another character's dialogue. For clips
of 12 seconds or less, prefer two active speakers, two or three short lines total,
and one clearly staged reaction beat; additional named characters may remain silent
unless the brief explicitly requires each to speak. Dialogue order, gaze shifts,
gestures and reactions must be chronological and physically possible in the stated
layout. Avoid ambiguous pronouns whenever more than one person is present.

Attached image thumbnails are visible evidence. Video and audio are NOT visible/audible
to you: use only the user's reference notes for their contents and
never invent observations, a transcript or voice properties. References remain
attached to the renderer. Use only supplied asset tags; no invented/renumbered tags.
Reference roles must not bleed together (identity is not automatically wardrobe,
pose is not automatically identity). User-requested changes override only the
specified attributes; preserve the remainder.

Aim for concrete, useful coverage, typically 250–650 words per video concept, not
padding or extra plot to meet a word count. Keep the finished H3 prompt below 7,000
characters. Longer reference descriptions should prioritize asset jobs, continuity,
timing and preservation constraints over ornamental prose.
Privately check scene specificity, reference fidelity, duration, composition and
sound before returning. Return ONLY JSON {"concepts":[{"title":"short title",
"prompt":"complete final H3 prompt"}]}. Every prompt must stand alone, not refer
to another variation. Put the mode's plain-text sections INSIDE each prompt string;
no Markdown fences, explanations, rendering settings or analysis in the prompt.
Each section name must be on its own line, with its content on the following line
and a blank line between sections. Use escaped newlines inside the JSON strings.
"""

BASE_SECTIONS = """
Each prompt uses these three fields in order:
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:
The integrated timeline begins with [Shot 1] and its opening-frame description.
"""


def generation_mode(recipe):
    if recipe.get("singleFrame"):
        return "reference-to-still" if recipe.get("h3Type") == "reference" else "text-or-keyframe-to-still"
    if recipe.get("h3Type") == "reference":
        return "reference-to-video"
    roles = {ref.get("kind") for ref in recipe.get("references", [])}
    first, last = "first_frame" in roles, "last_frame" in roles
    if first and last:
        return "first-and-last-frame"
    if first:
        return "first-frame"
    if last:
        return "last-frame"
    return "text-to-video"


def idea_system_prompt():
    """Creative planning only; the normal H3 enhancer writes the final scripts."""
    return """
Develop distinct, concrete ideas from the user's creative brief. This is the IDEA
stage, not the final MiniMax H3 prompt writer. A separate H3 enhancer will expand
each idea into the correct production prompt using the original brief and media.
Return only JSON {"concepts":[{"title":"short title","prompt":"idea direction"}]}.
Each idea should be 80–160 words: a specific setting, subject action/performance,
visual hook, camera intention, sound intention and ending that fit the duration.
Vary the actual premise and observable execution, not just adjectives or lighting.
Preserve the entire original brief, named identities, exact quoted dialogue,
wardrobe, reference roles, requested language/style, and all explicit constraints.
Do not invent additional people, dialogue, text, products or effects unless the
brief allows them. Preserve a requested silent scene or continuous shot.
Treat all views of one reference character sheet as ONE person, never a collage.
Image thumbnails are visible. Video and audio contents are NOT visible/audible
at this planning stage: use only the reference notes for those assets; never
claim observations of unseen media. Do not reassign reference tags or roles.
Do not write H3 section headings, mandatory timestamp grids, repeated production
checklists, or a final shooting script. Make the idea itself clear and interesting.
""" + H3_SKIN_COLOR_ARTIFACT_GUARD + H3_FACIAL_ACTION_ARTIFACT_GUARD


def system_prompt(recipe):
    mode = generation_mode(recipe)
    if recipe.get("singleFrame"):
        reference_guide = """
Use the supplied asset tags exactly and preserve their requested identity, object,
style, or composition roles. Attached image/keyframe thumbnails are visible; video
and audio contents are not, so use only the user's notes for those assets. Do not
invent observations about unseen media.
""" if recipe.get("references") else """
There are no visual references. Build the entire image from the written brief.
Do not refer to unseen assets or use Picture/Video/Audio tags.
"""
        return """
You are the visual director for an experimental MiniMax H3 still-image output.
Create distinct, fully realized image concepts—not video scripts. The renderer
samples H3's minimum five-frame latent and saves only the middle frame, so describe
one decisive frozen moment that reads clearly as a single image.

For every concept specify the subject's appearance, wardrobe or materials, pose and
expression; foreground/background layout; environment and props; camera framing;
the supplied aspect ratio; motivated lighting and its direction; palette, texture,
focus and atmosphere. Preserve named people, products, text, reference roles and
all explicit constraints. Do not add timelines, shot changes, motion progression,
dialogue, soundscape, music, timestamps, or audiovisual section headings. Avoid
generic quality filler. Each prompt must stand alone and should normally be
120–250 concrete words.
""" + H3_SKIN_COLOR_ARTIFACT_GUARD + H3_FACIAL_ACTION_ARTIFACT_GUARD + reference_guide + """
Return ONLY JSON {"concepts":[{"title":"short title","prompt":"complete final still-image prompt"}]}.
Return exactly the requested concept count with meaningfully different composition
or creative execution. No Markdown fences, explanations, settings, or analysis.
"""
    if mode == "reference-to-video":
        guide = """
MODE: Reference-to-video. Use exactly six sections:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
Define stable <Subject N> identities and explicitly bind supplied <Picture N>,
<Video N>, <Audio N> to their requested roles. Describe visible image details,
and use reference notes for withheld video/audio. Do not claim to have heard audio
or watched footage. Preserve requested roles even when contents are unavailable;
do not invent content to fill the gap. summary states the intended result and
reference relationships. retention_analysis states preserved/transferred/modified
attributes with fully_preserved, partially_preserved, attribute_transfer or
weak_reference; audio uses fully_copy/partially_copy only for waveform reuse,
reference for timbre/style. Never mark newly invented scene details as retained.
detailed_description begins with [Shot 1] and gives the full scene and temporal
execution, not merely a list of edits. Account for every supplied reference and
keep each role and tag consistent across sections.
For every supplied asset, state one or more explicit jobs in subject_definitions and
repeat the important mapping in summary/retention_analysis. For editing, pair each
requested substitution, removal, addition or relight with the exact elements and
attributes that must remain unchanged. detailed_description follows the complete
timed visual-script contract from the common instructions.
"""
    elif mode == "text-to-video":
        guide = BASE_SECTIONS + """
MODE: Text-to-video, NO visual references. Build the complete opening image from
scratch: who/what is visible, distinctive appearance/wardrobe, foreground/background
layout, concrete setting/props, light source, palette/materials and composition.
Then describe the full action, performance, camera and sound progression. The
renderer has ONLY this prompt: never rely on 'the reference', an unseen location,
'same as before', or a title alone to establish identity/environment. Do not emit
Picture/Video/Audio asset tags. A short idea sentence with dialogue is insufficient.
"""
    else:
        guide = BASE_SECTIONS + f"""
MODE: {mode}. Supplied frames are literal endpoints, not mood-board references.
First frame = opening; last frame = closing. Never swap them. Anchor subject
identity, wardrobe, composition, lighting and key objects to the visible endpoint.
With both endpoints, describe plausible continuous motion connecting them; with
only first, develop forward from it; with only last, create a compatible lead-in
that arrives at it. Do not force an incompatible setting, transformation or cut.
Spend detail on changes over time, performance and sound rather than repeatedly
redescribing the still. Explicitly reach the closing frame when one is supplied.
"""
    return COMMON + guide
