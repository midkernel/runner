"""Offline Git fixtures for the image selector and its always-run status gate."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_image_affected.py"
SPEC = importlib.util.spec_from_file_location("ci_image_affected", SCRIPT)
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
COPY_INPUTS = [
    "pyproject.toml", "README.md", "LICENSE", "src/example.py", "pipelines/example.py",
    "scripts/entrypoint.sh", "scripts/node-env.sh", "scripts/kimi-wrapper.sh",
]


class Repository:
    def __init__(self, path):
        self.path = path
        path.mkdir()
        self.git("init", "--quiet", "--template=")
        self.write("Dockerfile", (ROOT / "Dockerfile").read_bytes())
        for name in [*COPY_INPUTS, "docs/old.md", "docs/second.md", ".dockerignore"]:
            self.write(name, f"original: {name}\n")
        self.base = self.commit([])
        self.git("update-ref", "HEAD", self.base)

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=CI fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.path, check=True, capture_output=True,
        ).stdout.decode("utf-8").strip()

    def write(self, name, data="changed\n"):
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    def commit(self, parents):
        self.git("add", "-A")
        tree = self.git("write-tree")
        return self.git("commit-tree", tree, *[arg for p in parents for arg in ("-p", p)], "-m", "fixture")

    def pr(self, merge_change=None):
        head = self.commit([self.base])
        if merge_change:
            merge_change()
        source = self.commit([self.base, head])
        self.git("update-ref", "HEAD", source)
        return {"GITHUB_EVENT_NAME": "pull_request", "PR_BASE_SHA": self.base,
                "PR_HEAD_SHA": head, "GITHUB_SHA": source}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repository = Repository(tmp_path / "repo")
    monkeypatch.chdir(repository.path)
    return repository


@pytest.fixture
def no_git(monkeypatch):
    def unexpected_git(*args, **kwargs):
        pytest.fail("this case must not inspect Git")
    monkeypatch.setattr(ci, "git", unexpected_git)
    return {"GITHUB_EVENT_NAME": "pull_request", "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40, "GITHUB_SHA": "c" * 40}


def skipped(env):
    return {**env, "PLAN_OUTCOME": "success", "PLAN_DECISION": "skip",
            "BUILDX_OUTCOME": "skipped", "BUILD_OUTCOME": "skipped"}


def test_ordinary_docs_add_and_modify_skip_and_gate_rechecks(repo, capsys):
    repo.write("docs/old.md")
    repo.write("docs/nested/new.rst")
    repo.write("docs/notes.txt")
    env = repo.pr()
    assert ci.decision(env) == "skip"
    ci.gate(skipped(env))
    assert "not executed" in capsys.readouterr().out


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "pull_request_target", "", "unknown"])
def test_non_pr_always_builds_without_git(no_git, event):
    env = {**no_git, "GITHUB_EVENT_NAME": event}
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("name", [
    *COPY_INPUTS, "Dockerfile", ".dockerignore", "Dockerfile.dockerignore",
    ".github/workflows/ci.yml", "scripts/ci_image_affected.py", "config.json",
    "new-unknown", "docs/script.py", "docs/config.yml", "docs/image.png", "DOCS/note.md",
])
def test_every_input_and_unknown_path_builds(repo, name):
    repo.write(name)
    repo.write("docs/old.md")
    env = repo.pr()
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("extra", ["\nCOPY . /app\n", "\nADD docs /docs\n", "\n# drift\n"])
def test_stale_dockerfile_map_builds_on_later_docs_only_pr(repo, extra):
    repo.write("Dockerfile", (ROOT / "Dockerfile").read_text() + extra)
    repo.base = repo.commit([repo.base])
    repo.write("docs/old.md")
    env = repo.pr()
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("name,target,extra", [
    ("pyproject.toml", "docs/build-project.txt", b"\n# changed build metadata\n"),
    (".dockerignore", "docs/context-filter.txt", b"\nsrc\n"),
])
def test_preexisting_input_symlink_requires_build_and_rejects_skip(repo, name, target, extra):
    original = (ROOT / name).read_bytes()
    repo.write(target, original)
    (repo.path / name).unlink()
    (repo.path / name).symlink_to(target)
    repo.base = repo.commit([repo.base])
    repo.git("update-ref", "HEAD", repo.base)
    repo.write(target, original + extra)
    env = repo.pr()
    commits = [env[key] for key in ("PR_BASE_SHA", "PR_HEAD_SHA", "GITHUB_SHA")]
    links = [repo.git("ls-tree", commit, "--", name) for commit in commits]
    assert len(set(links)) == 1 and links[0].startswith("120000 blob ")
    for commit in commits:
        assert repo.git("ls-tree", commit, "--", target).startswith("100644 blob ")
        assert hashlib.sha256(ci.git("show", f"{commit}:Dockerfile")).hexdigest() == ci.DOCKERFILE_SHA256
    for commit in commits[1:]:
        assert ci.git("diff", "--name-status", "-z", repo.base, commit) == b"M\0" + target.encode() + b"\0"
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("kind", ["symlink", "gitlink"])
def test_preexisting_unsupported_tree_shape_builds(repo, kind):
    if kind == "symlink":
        (repo.path / "docs/unrelated-link").symlink_to("../README.md")
    else:
        (repo.path / "vendor").mkdir()
        repo.git("update-index", "--add", "--cacheinfo", f"160000,{repo.base},vendor")
    repo.base = repo.commit([repo.base])
    repo.write("docs/old.md")
    env = repo.pr()
    for key in ("PR_BASE_SHA", "PR_HEAD_SHA", "GITHUB_SHA"):
        with pytest.raises(ValueError):
            ci.tree(env[key])
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("depth", [1, 2])
def test_shallow_checkout_eligibility_and_missing_parent_fallback(repo, tmp_path, monkeypatch, depth):
    repo.write("docs/old.md")
    env = repo.pr()
    shallow = tmp_path / "shallow"
    repo.git("clone", "--quiet", "--no-local", f"--depth={depth}", str(repo.path), str(shallow))
    monkeypatch.chdir(shallow)
    assert ci.git("rev-parse", "--is-shallow-repository") == b"true\n"
    if depth == 2:
        assert ci.git("rev-list", "--parents", "-n", "1", env["GITHUB_SHA"]).decode().split() == [
            env[key] for key in ("GITHUB_SHA", "PR_BASE_SHA", "PR_HEAD_SHA")
        ]
        assert ci.decision(env) == "skip"
        ci.gate(skipped(env))
    else:
        assert ci.decision(env) == "build"
        with pytest.raises(subprocess.CalledProcessError):
            ci.gate(skipped(env))


@pytest.mark.parametrize("old,new", [
    ("docs/old.md", "docs/renamed.md"), ("README.md", "docs/moved.md"),
    ("docs/old.md", "README.md"), ("docs/old.md", "unknown.md"),
])
def test_renames_expose_both_names_and_build(repo, old, new):
    (repo.path / old).rename(repo.path / new)
    env = repo.pr()
    raw = ci.git("diff", "--name-status", "-z", "--no-renames", repo.base, env["PR_HEAD_SHA"], "--")
    assert old.encode() in raw.split(b"\0")
    assert new.encode() in raw.split(b"\0")
    assert ci.decision(env) == "build"


@pytest.mark.parametrize("name", ["docs/old.md", "README.md", "scripts/node-env.sh"])
def test_deletions_build(repo, name):
    (repo.path / name).unlink()
    assert ci.decision(repo.pr()) == "build"


@pytest.mark.parametrize("kind", ["symlink", "executable", "type-change"])
def test_non_regular_docs_build(repo, kind):
    if kind == "executable":
        (repo.path / "docs/old.md").chmod(0o755)
    elif kind == "type-change":
        (repo.path / "docs/old.md").unlink()
        (repo.path / "docs/old.md").symlink_to("../README.md")
    else:
        (repo.path / "docs/link.md").symlink_to("../README.md")
    assert ci.decision(repo.pr()) == "build"


@pytest.mark.parametrize("name", [
    "docs/a space.md", "docs/a\ttab.md", "docs/a\nnewline.md", "docs/é日本.md",
    "docs/$(touch INJECTED).md", "docs/`touch INJECTED`.md", "docs/--help.md",
])
def test_weird_utf8_paths_are_nul_safe_and_never_shell_code(repo, name):
    repo.write(name)
    env = repo.pr()
    assert ci.decision(env) == "skip"
    ci.gate(skipped(env))
    assert not (repo.path / "INJECTED").exists()


def test_invalid_utf8_filename_builds(repo):
    with open(os.fsencode(repo.path) + b"/docs/invalid-\xff.md", "wb") as output:
        output.write(b"text")
    assert ci.decision(repo.pr()) == "build"


@pytest.mark.parametrize("key", ["PR_BASE_SHA", "PR_HEAD_SHA", "GITHUB_SHA"])
@pytest.mark.parametrize("value", ["", "main", "-h", "$(touch INJECTED)", "a" * 40 + "\n"])
def test_missing_or_invalid_identity_builds(no_git, key, value):
    env = {**no_git, key: value}
    assert ci.decision(env) == "build"
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("key", ["PR_BASE_SHA", "PR_HEAD_SHA", "GITHUB_SHA"])
def test_absent_valid_identity_builds(repo, key):
    repo.write("docs/old.md")
    env = {**repo.pr(), key: "0" * 40}
    assert ci.decision(env) == "build"
    with pytest.raises(subprocess.CalledProcessError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("which", ["base", "head", "merge", "doc_blob", "source_blob", "tree"])
def test_missing_objects_build(repo, which):
    repo.write("docs/old.md")
    env = repo.pr()
    oid = {
        "base": repo.base, "head": env["PR_HEAD_SHA"], "merge": env["GITHUB_SHA"],
        "doc_blob": repo.git("rev-parse", "HEAD:docs/old.md"),
        "source_blob": repo.git("rev-parse", "HEAD:README.md"),
        "tree": repo.git("rev-parse", "HEAD:docs"),
    }[which]
    (repo.path / ".git/objects" / oid[:2] / oid[2:]).unlink()
    assert ci.decision(env) == "build"


def test_empty_diff_builds(repo):
    assert ci.decision(repo.pr()) == "build"


def test_merge_only_source_change_builds(repo):
    repo.write("docs/old.md")
    env = repo.pr(lambda: repo.write("README.md"))
    assert ci.decision(env) == "build"


def test_wrong_checkout_or_merge_parents_build(repo):
    repo.write("docs/old.md")
    env = repo.pr()
    repo.git("update-ref", "HEAD", env["PR_HEAD_SHA"])
    assert ci.decision(env) == "build"
    env["GITHUB_SHA"] = env["PR_HEAD_SHA"]
    assert ci.decision(env) == "build"


def test_gate_rechecks_current_checkout_after_planning(repo):
    repo.write("docs/old.md")
    env = repo.pr()
    assert ci.decision(env) == "skip"
    repo.write("README.md")
    with pytest.raises(ValueError):
        ci.gate(skipped(env))


@pytest.mark.parametrize("data", [
    b"", b"M\0docs/old.md", b"M\0", b"M\0docs/old.md\0M\0",
    b"M\0docs/old.md\0",  # whole second record omitted: tree comparison catches it
    b"M\0docs/old.md\0M\0docs/old.md\0", b"R100\0docs/old.md\0docs/new.md\0",
    b"M\0docs/../README.md\0", b"M\0docs/\xff.md\0", b"M\0\0", b"wat\0docs/old.md\0",
])
def test_malformed_and_incomplete_diffs_build(repo, monkeypatch, data):
    repo.write("docs/old.md")
    repo.write("docs/second.md", "different\n")
    env = repo.pr()
    real_git = ci.git
    monkeypatch.setattr(ci, "git", lambda *args, **kw: data if args[0] == "diff" else real_git(*args, **kw))
    assert ci.decision(env) == "build"
    with pytest.raises((ValueError, UnicodeError)):
        ci.gate(skipped(env))


@pytest.mark.parametrize("command,data", [
    ("ls-tree", b""), ("ls-tree", b"100644 blob invalid\tdocs/a.md\0"),
    ("ls-tree", b"100644 blob " + b"1" * 40 + b"\tdocs/\xff.md\0"),
    ("cat-file", b""), ("cat-file", b"bad object result\n"),
])
def test_incomplete_or_malformed_supporting_results_build(repo, monkeypatch, command, data):
    repo.write("docs/old.md")
    env = repo.pr()
    real_git = ci.git
    def damaged(*args, **kw):
        if args[0] == command and (command != "cat-file" or "--batch-check" in args):
            return data
        return real_git(*args, **kw)
    monkeypatch.setattr(ci, "git", damaged)
    assert ci.decision(env) == "build"


@pytest.mark.parametrize("error", [OSError("fixture"), subprocess.CalledProcessError(1, "git"), ValueError("fixture")])
def test_classifier_errors_require_full_build(no_git, monkeypatch, error):
    def broken(_env):
        raise error
    monkeypatch.setattr(ci, "eligible_skip", broken)
    assert ci.decision(no_git) == "build"


@pytest.mark.parametrize("key,values", [
    ("PLAN_OUTCOME", ["", "failure", "cancelled", "skipped"]),
    ("PLAN_DECISION", ["", "unknown", "false"]),
    ("BUILDX_OUTCOME", ["", "failure", "cancelled", "skipped"]),
    ("BUILD_OUTCOME", ["", "failure", "cancelled", "skipped"]),
])
def test_gate_rejects_missing_outputs_failed_or_skipped_work(key, values):
    env = {"PLAN_OUTCOME": "success", "PLAN_DECISION": "build",
           "BUILDX_OUTCOME": "success", "BUILD_OUTCOME": "success"}
    ci.gate(env)
    for value in values:
        with pytest.raises(ValueError):
            ci.gate({**env, key: value})
    with pytest.raises(ValueError):
        ci.gate({k: v for k, v in env.items() if k != key})


@pytest.mark.parametrize("outcome", ["", "failure", "cancelled", "success"])
def test_skip_gate_rejects_inconsistent_step_outcomes(repo, outcome):
    repo.write("docs/old.md")
    env = skipped(repo.pr())
    for key in ("BUILD_OUTCOME", "BUILDX_OUTCOME"):
        with pytest.raises(ValueError):
            ci.gate({**env, key: outcome})


def test_cli_plan_and_gate_and_crashed_classifier(repo, tmp_path):
    repo.write("docs/old.md")
    env = {**os.environ, **repo.pr(), "GITHUB_OUTPUT": str(tmp_path / "output")}
    plan = subprocess.run([sys.executable, str(SCRIPT), "plan"], env=env, capture_output=True)
    assert plan.returncode == 0
    assert (tmp_path / "output").read_text() == "decision=skip\n"
    gate = subprocess.run([sys.executable, str(SCRIPT), "gate"], env=skipped(env), capture_output=True)
    assert gate.returncode == 0
    assert b"not executed" in gate.stdout
    missing = subprocess.run([sys.executable, str(SCRIPT), "gate"], env=env, capture_output=True)
    assert missing.returncode != 0
    # An unexpected classifier exception cannot write a success output.
    (tmp_path / "output").unlink()
    code = (
        "import runpy,sys; m=runpy.run_path(sys.argv[1]); "
        "m['decision'].__globals__['eligible_skip']=lambda e: 1/0; "
        "sys.argv=['ci','plan']; m['main']()"
    )
    crashed = subprocess.run([sys.executable, "-c", code, str(SCRIPT)], env=env, capture_output=True)
    assert crashed.returncode != 0
    assert not (tmp_path / "output").exists()


def test_workflow_preserves_status_and_unconditional_unit_job():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    unit = workflow.split("  test:\n", 1)[1].split("  publisher-readiness:\n", 1)[0]
    assert "name: Unit tests" in unit and "pytest -q" in unit
    assert "if:" not in unit and "needs:" not in unit
    image = workflow.split("  image:\n", 1)[1].split("  oidc-claims:\n", 1)[0]
    assert "name: Docker build" in image
    assert "ref: ${{ github.sha }}" in image and "fetch-depth: 2" in image
    assert "if: always()" in image
    assert "PLAN_OUTCOME: ${{ steps.image-plan.outcome }}" in image
    assert "PLAN_DECISION: ${{ steps.image-plan.outputs.decision }}" in image
    assert "BUILD_OUTCOME: ${{ steps.build.outcome }}" in image
    assert "cache-from: type=gha" in image and "cache-to: type=gha,mode=max" in image
    assert "continue-on-error" not in image
    assert "paths:" not in workflow and "paths-ignore:" not in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number)" in workflow
    assert "format('run-{0}-{1}', github.run_id, github.run_attempt)" in workflow
