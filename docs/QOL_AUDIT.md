# QoL audit — video preparation and rental contracts

This pass reviews the current public controller without paid capacity, rendering,
credential changes, or queue mutations. Changes are isolated on the QoL branch.

## Fixed

| Finding | Change and verification |
| --- | --- |
| Video preparation interpreted crop values as corners, while reference media uses normalized x/y/width/height. Offset crops could fail or select the wrong region. | Use the reference coordinate contract, reject invalid/boolean values early, invalidate old prepared-video cache keys, and update the pinned overlay checksum. Regression covers a non-zero crop origin. |
| AI motion previews center-cropped footage while the render preparation preserves it with padding. | Match fit-and-pad framing. Synthetic wide-to-portrait test checks padding and source preservation. |
| The preprocessor displayed a maximum requested frame count as though it described a shorter clip. | Count stored video packets and report actual frames and duration. A 1–4-second selection produces 72 frames at 24 fps, not the 124-frame generation ceiling. |
| Selected-range playback could exceed the duration that export would use. | Playback obeys the selected end and the same H3 frame-grid ceiling; the label explains a capped selection. H3's 17k+5 grid can exceed the requested nominal duration. |
| Cancelling a preview request released its lock while its CPU thread continued encoding. | The CPU worker owns the lock until completion. Cancellation/concurrency regression verifies a second preview gets a conflict. |
| Closing and reopening a processing window could receive an old request's error or button state. | Guard completion/error updates by editor identity, clear identity on close, and reset controls on reopen. |
| The video modal had no dedicated layout and a full-width Close button. | Add a compact themed dialog with a sticky heading, collapsed processing explanation, result visibility, and phone-sized tap targets. |
| Video failures hid useful distinctions. | Separate dependency/source availability, timeout, empty interval, and decode failures. Return a diagnostic ID; retain the underlying exception in the controller log without returning internal paths in the response. |
| A multi-GPU request could start one manager, fail on the next, and return only a generic failure. | Preserve accepted and failed rental IDs in a partial-success response, stop remaining starts, and distinguish a confirmed zero-allocation manager failure. Tests never invoke a provider. |
| Lost/unreadable launch responses looked like ordinary errors and immediately enabled another rental. | Extract a tested cloud-request helper. Show uncertain/billable-state guidance and hold further launches in that dialog until the operator reviews Rentals/provider state. No automatic retry. |
| SSH tunnel evidence was missing from retained diagnostics. | Include a redacted SSH tunnel stage in individual and combined diagnostics. |

## Remaining checks and follow-up

- A real RunPod/Vast connection interruption still needs an explicitly authorized
  paid test. Server-side SSH keepalives apply to newly launched RunPods; old
  running daemons are not silently reconfigured.
- The launch review guard is browser-local, not durable server idempotency.
  After a lost response, operators must reconcile provider/controller records
  before reloading and launching again. Durable request IDs and restart-safe
  reconciliation deserve a separate contract change.
- Browser checks cover desktop and a 390px viewport with no horizontal dialog
  overflow. Physical iOS/Android touch, seeking, and codec behavior remain to be
  checked on devices.
- Existing public frontend/backend files still contain legacy model-family
  branches. Removing those safely needs a separate dependency/usage pass; this
  change does not expand supported models or remove runtime allowlists.

## Validation

Focused and full pytest suites, JavaScript syntax checks, real CPU-only synthetic
video conversion/frame counting, pinned-source checks, and an isolated browser
smoke test. No GPU job, LLM request, rental, credential change, or live dispatcher
restart is needed for this audit.
