# Runner CI image decision

Baseline: [mail#20 evidence](https://github.com/midkernel/mail/issues/20#issuecomment-6559468273),
with runner source pinned to [2c1e86264834cfc694f3c06406004d78ff8b25c5](https://github.com/midkernel/runner/tree/2c1e86264834cfc694f3c06406004d78ff8b25c5).
The three sampled successful attempts were:

- [PR run 41192009694, attempt 1](https://github.com/midkernel/runner/actions/runs/41192009694/attempts/1): image job 99s, image build step 81s, unit job 14s, workflow wall 102s.
- [PR run 41291856125, attempt 1](https://github.com/midkernel/runner/actions/runs/41291856125/attempts/1): image job 39s, image build step 22s, unit job 13s, workflow wall 42s.
- [Main run 41292031765, attempt 1](https://github.com/midkernel/runner/actions/runs/41292031765/attempts/1): image job 92s, image build step 74s, unit job 16s, workflow wall 95s.

These are timestamp deltas, not population percentiles. Workflow wall is creation
through last job completion, not push latency or the sum of parallel jobs. Unit
tests already run independently. BuildKit GHA caching already exists; its hit
quality and restoration from main were not measured. No broken-cache claim or
speedup ratio follows from this sample.

Only runs of the same PR share a cancelling concurrency group. Every other run
has a unique run-ID/attempt group with cancellation disabled. The existing unit
job always runs. The existing `Docker build` job plans its image work, then either
executes the unchanged build or independently validates an eligible skip in an
`always()` final gate. A skipped image is explicitly reported as not executed.
Main and manual runs always require a successful image build. Publisher needs,
activation, AWS/OIDC, ECR, triggers, permissions, job names and runtime are unchanged.

## Reviewed local build-input closure

The exact [Dockerfile at the baseline](https://github.com/midkernel/runner/blob/2c1e86264834cfc694f3c06406004d78ff8b25c5/Dockerfile)
has SHA-256 `3fd8bae83850082bb632cf5c67720841edebed130c0cd50b645c7db9bbc849d5`.
Its complete local context inputs are:

- `pyproject.toml`, `README.md`, `LICENSE` → `/opt/midkernel/src/`.
- All of `src/` and `pipelines/` → the respective directories below that location.
- `scripts/entrypoint.sh` → `/usr/local/bin/midkernel-agentflow-entrypoint`.
- `scripts/node-env.sh` → `/opt/midkernel/node-env.sh`.
- `scripts/kimi-wrapper.sh` → `/usr/local/bin/kimi`.
- The Dockerfile and `.dockerignore` control the build and context filtering.

There are six explicit COPY instructions, no ADD, context bind mount, wildcard
COPY, whole-context COPY or extra stage. The first RUN installs external base
dependencies before any repository files are copied. The later RUN changes
permissions and installs the already-copied project; its metadata reads README
and packages `src/midkernel_runner`. `docs/` is never copied or mounted and is not
available to either RUN. The exclusion does not depend on `.dockerignore` hiding
Markdown. A reference to `docs/publishing.md` in a Dockerfile comment is inert.

The helper checks this exact Dockerfile fingerprint and the complete tree shape
in base, head and merge source. Every file entry in all three trees must be a
regular `100644` or `100755` blob. **Any symlink or gitlink anywhere requires a
full build**, even when unchanged: an existing `pyproject.toml` or `.dockerignore`
symlink into `docs/` would otherwise make its text target a hidden build input.
The baseline contains no symlinks or gitlinks. **Any Dockerfile drift also requires
a full build**, including future `COPY .`, ADD or mounts. Do not automatically
regenerate the fingerprint: review the entire input closure and the negative
fixtures before deliberately updating it.

Only additions/modifications of regular, non-executable `.md`, `.rst` or `.txt`
files below `docs/` qualify. All other paths build, including README, LICENSE,
every COPY input, workflow/helper/config changes, `.dockerignore` and unknown
paths. Deletes, renames (both names via `--no-renames`), symlinks and mode/type
changes build. The checkout is the event SHA, normally the PR test merge, at
constant fetch depth 2. Only its immediate parents and the direct base/head/merge
trees are needed; unavailable objects still require a full build. A skip requires
those exact two merge parents, a clean checkout, and eligible changes from base
to both head and merge.

Changed-path data uses checked, complete local Git subprocess output with NUL
separators and strict UTF-8 decoding. Separate tree comparisons verify the entire
path set, including detecting truncation at a record boundary; object checks
reject missing blobs. Empty, malformed, ambiguous or incomplete data selects a
full build or fails. The gate recomputes skip eligibility instead of trusting a
planner boolean. Missing outputs, skipped/failed planning and missing, skipped,
failed or cancelled required builds cannot pass it.

## Offline validation and future hosted measurements

Run `python -m pytest -q` after installing the existing `.[dev]` dependencies.
The corrected implementation validation used Python 3.13.15 and the existing
project dependencies: **323 passed**, comprising 109 CI cases and 214 existing
unit tests, with zero skips. Early-exit event/invalid-identity/error tests avoid Git
setup; Git/tree/corruption/CLI/shape cases use isolated temporary repositories.
Network syscalls were denied for that local run and all its subprocesses. The
fixtures cover every input above, main/manual, ordinary and unusual docs paths,
Dockerfile drift, pre-existing source/config symlinks, gitlinks, real depth-2
checkouts and insufficient-depth fallback, renames/deletions, invalid
encodings/identities, missing objects, malformed/truncated results, classifier
errors, merge-source mismatches, dirty checkouts and invalid gate outcomes.
Missing-object tests never share mutable object stores. Docker was not executed
locally; local fixture timings are not hosted speedup measurements.

**Hosted after metrics are pending.** The implementation PR changes workflow and
helper files, so it must exercise the full-build path. Compare later natural
docs-only eligible PRs separately from full-source PRs; keep main/manual,
failures, cancellations and skips separate too. Record run/attempt/SHA, runner
version, checkout/planning/gate overhead, build-step and job durations, scheduling
proxy, workflow wall and cache evidence when available. Bounded checkout and the
new fixtures still add work; net savings and eligible-doc frequency remain
unmeasured. No
manual dispatch, rerun, publisher activation or production publishing is part of
this validation.

## Contributor-hook follow-up

The historical pending paragraph above describes the image implementation at
submission. The subsequent
[runner#21 acceptance](https://github.com/midkernel/runner/pull/21#issuecomment-6560596812)
records merge `28e520198b731478ece2910e9516a1b216148e03` and these attempt-1 results:

- [PR 41325101194](https://github.com/midkernel/runner/actions/runs/41325101194/attempts/1): 24s workflow wall proxy, 21s image job / 2s build step; 19s unit job, 323 passed in 6.57s.
- [Main 41325695439](https://github.com/midkernel/runner/actions/runs/41325695439/attempts/1): 22s workflow wall proxy, 15s image job / 3s build step; 19s unit job, 323 passed in 6.35s.

Both image builds executed and each log contains 10 cached BuildKit records.
Those build-time reductions cannot be attributed to image skipping or hooks.
The prior samples above remain 42s/102s PR and 95s main; the unit job increased
from 13–16s to 19s with the added CI cases. Publisher activation was skipped
on PR and checked on main; ECR remained skipped. No publishing or live canary
was activated. Eligible docs-only skip savings and real PR cancellation remain
unobserved.

The follow-up [contributor setup](prepush.md) installs only the existing config
contract test as a local quick gate. The workflow, image helper and all existing
tests are unchanged from that merged base. Full CI remains intact. **Hosted
after results for this hook adoption are pending.** Local offline checks and
local bare-push fixtures establish hook behavior, not hosted timing savings,
runtime coverage or all-history validation. No CI rerun/dispatch was requested.
