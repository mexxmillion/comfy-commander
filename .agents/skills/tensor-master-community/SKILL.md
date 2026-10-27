---
name: tensor-master-community
description: Operate the public Tensor Master Community controller through its guarded APIs.
---

# Tensor Master Community

Use this skill when asked to inspect profiles, prepare a supported graph, submit
or monitor a Tensor Master Community job, or explain the public worker setup.

1. Read `AGENTS.md` and `GET /api/v1/community/agent-manifest`.
2. Check `GET /health` and `GET /api/v1/community/profiles` before mutation.
   Prefer `scripts/tensor_master_client.py`; it does not print the credential.
3. Never request credentials in chat. Direct the human to `/setup`.
4. Do not rent a GPU or render unless explicitly authorized. Record the chosen
   profile and cost limits before a rental.
5. Submit an allowlisted graph once to `POST /api/v1/jobs`, retain `job_id`, and
   poll its job endpoint. Reconcile a timeout before retrying.
6. For character replacement, call the preparation endpoint, submit its graph,
   and stop for visual review of mask ownership and the inverted target guide.
   Only an accepted guide proceeds to H3 Ref2VA. Do not add image guide nodes.
7. Report rendered and QC-accepted as distinct states.
