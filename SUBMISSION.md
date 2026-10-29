# Open Source Higgsfield submission checklist

The competition closes **October 30, 2026 at 9:00 a.m. Pacific**. Tensor Master
Community is positioned for Best Overall, Most Innovative, Most Viral and the
MiniMax H3 ecosystem bonus. Do not claim Open Weights Only while OpenRouter is
enabled or if the demo uses a closed/partner model.

## Before publishing

- [ ] Create a new public repository from a single clean root commit. Do not
      publish the production repository history.
- [ ] Confirm the license and every bundled node's license.
- [ ] Run `pytest tests/test_community.py`, Ruff and the secret/private-path scan.
- [ ] Build and start the Docker image on a host with Docker running.
- [ ] Test first-run setup with fresh storage and no existing credentials.
- [ ] Accept the SAM3 upstream terms with a dedicated test Hugging Face account.
- [ ] Launch one explicitly authorized low-cost worker, render the demo shot,
      destroy the worker, and retain cost and job receipts.
- [ ] Verify the accepted SAM3 mask, target-only guide, H3 motion, expressions,
      seams and continuous source audio.
- [ ] If the final rules require a Comfy Developer Platform deployment, package
      and deploy the same pinned Comfy graph there and show its endpoint in the
      demo. The self-hosted RunPod/Vast path is not evidence of that deployment.

## Two-minute demo outline

**0:00–0:12 — Result first.** Show the source performance beside the finished
character replacement. State: open-source, ComfyUI, MiniMax H3.

**0:12–0:30 — One-page setup.** Scroll through the local password and optional
RunPod/Vast, Hugging Face and OpenRouter connections. Emphasize that connecting
a key does not rent a GPU.

**0:30–0:52 — Ask Codex.** Say: “Prepare a character replacement for the person
in the red jacket, use a community H3 worker, and stop for mask review.” Show the
repository skill and agent manifest being discovered.

**0:52–1:16 — The differentiator.** Show the SAM3 mask and target-only inverted
video. Explain that the system tracks one reviewed performer and passes the
aligned video to Ref2VA without image guide nodes.

**1:16–1:38 — Portable compute.** Show the profile picker, price/lifetime limits,
one worker heartbeat and the job receipt. Do not imply automatic rental without
human authorization.

**1:38–1:54 — QC.** Show expression/choreography/seam review and preserved source
audio. Distinguish “rendered” from “accepted.”

**1:54–2:00 — Close.** Show the public repo, AGPL license and quick-start command.

## Submission package

- public repository URL
- short setup instructions (README)
- demo video no longer than the form permits
- one architecture still or diagram for social posts
- exact model/license acknowledgements
- tested commit SHA
