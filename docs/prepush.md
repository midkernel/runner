# Contributor pre-push checks

Explicit developer setup (Python >=3.11, Git >=2.31, POSIX shell):

```sh
python3 scripts/setup-dev.py
```

Setup creates a regular `.venv` if absent, installs the existing `.[dev]`
dependencies with pip, then installs/verifies the default shared Git pre-push
dispatcher and enrolls **this worktree only**. It refuses a symlink `.venv`;
repair an incomplete environment explicitly. Normal pip cache/index settings
apply during setup; a prepared offline wheelhouse can use `PIP_NO_INDEX=1`
and `PIP_FIND_LINKS`. Setup is the only installation step: pushes never install
or upgrade dependencies. Ordinary `pip install -e '.[dev]'`, including CI's
existing install, does not enroll a checkout.

The hook runs this small existing contract test with plugin autoload disabled:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -I -m pytest -q -p no:cacheprovider tests/test_config.py
```

This checks config defaults, aliases, timeout budgets and invalid values. These
are **contract tests, not formatting, linting or typechecking**; no such tools
are configured here. The selected tests pass explicit fixture dictionaries.
Their repository import closure is `midkernel_runner/__init__.py`, `config.py`
and the lazy `openrouter_generations.py` object-key helper. That module's network
capture/patch functions are defined but never called on this path. No runtime
entrypoint, provider login, publisher, Docker, OIDC/ECR or runtime API is run.
CI retains its full unit suite and existing image/result/publisher gates.

The shared dispatcher lives at Git's default common `hooks/pre-push` path;
`midkernel-prepush-v1` in each worktree's absolute Git directory controls opt-in.
Unenrolled sibling worktrees do nothing. Setup does not change Git config, index,
identity or HEAD. It refuses configured `core.hooksPath`, custom hooks, hook
symlinks and invalid enrollment markers instead of replacing them. Coordinate
manual integration with their owner. Repeating setup is safe for a matching
executable dispatcher. To stop enrollment, remove only your worktree's marker;
do not remove a dispatcher shared by other enrolled worktrees.

A supported content push is one branch update whose tip is the current clean
HEAD (branch, literal `HEAD`, or full object ID). The gate checks before and after
tests and rejects staged/unstaged changes, nonignored untracked files,
assume-unchanged/skip-worktree entries and submodules. Missing tools, venv,
adapter or Git/ref errors fail closed with a diagnostic. Commit or stash your
own changes explicitly; the hook never stashes. Tag creation/update, multiple
refs and non-HEAD tips are unsupported. Empty updates and single branch/tag
deletions have no source to check. Prepare/check out other tips separately.

This is contributor feedback, not enforcement or a hermetic snapshot. It tests
the current tip, not every intermediate commit. Ignored files, the prepared
venv and inherited `PATH` remain local inputs; Git's ordinary stat comparisons
and concurrent edits remain limitations. Repository-controlled checks are
trusted. Checks receive a reduced environment and temporary home/cache, **not
an OS/network sandbox**. The offline implementation fixtures separately deny
network syscalls. `--no-verify`, configuration changes or removing hooks/markers
can bypass this gate; CI remains authoritative. See the
[CI follow-up](ci-image-validation.md#contributor-hook-follow-up) for timings
and their limits.
