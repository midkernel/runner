"""Conservative PR image skip; no dependencies, network calls or Docker execution.

See docs/ci-image-validation.md before updating the reviewed Dockerfile hash.
"""

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys


DOCKERFILE_SHA256 = "3fd8bae83850082bb632cf5c67720841edebed130c0cd50b645c7db9bbc849d5"
SHA = re.compile(r"[0-9a-f]{40}")


def git(*args, input=None):
    # No shell, external diff, text conversion, paging or replacement objects.
    return subprocess.run(
        ["git", "--no-pager", "--no-replace-objects", *args],
        input=input, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def nul_fields(data):
    if not data or not data.endswith(b"\0"):
        raise ValueError("empty or unterminated Git result")
    fields = data[:-1].split(b"\0")
    if any(not field for field in fields):
        raise ValueError("empty Git field")
    return fields


def path_name(raw):
    name = raw.decode("utf-8", errors="strict")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("ambiguous Git path")
    return name


def tree(commit):
    entries = {}
    for record in nul_fields(git("ls-tree", "-rz", "--full-tree", commit)):
        metadata, raw_path = record.split(b"\t", 1)
        # Even unchanged symlinks can redirect COPY/config inputs into docs.
        # Every base/head/merge entry must be a regular file; gitlinks also build.
        if not re.fullmatch(rb"(100644|100755) blob [0-9a-f]{40}", metadata):
            raise ValueError("unsupported or malformed tree entry")
        name = path_name(raw_path)
        if name in entries:
            raise ValueError("duplicate tree entry")
        entries[name] = metadata
    return entries


def docs_changes(base, target, before, after):
    fields = nul_fields(git(
        "diff", "--name-status", "-z", "--no-renames", "--no-ext-diff",
        "--no-textconv", "--ignore-submodules=none", base, target, "--",
    ))
    if len(fields) % 2:
        raise ValueError("incomplete changed-path record")
    changes = {}
    for raw_status, raw_path in zip(fields[::2], fields[1::2]):
        name = path_name(raw_path)
        if raw_status not in (b"A", b"M", b"D", b"T") or name in changes:
            raise ValueError("ambiguous changed-path record")
        changes[name] = raw_status

    # An independent tree comparison catches omitted records, even truncation
    # exactly at a NUL record boundary. No API pagination or output-size limit.
    expected = {
        name for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    }
    if not expected or set(changes) != expected:
        raise ValueError("incomplete changed-path set")
    for name, status in changes.items():
        if not name.startswith("docs/") or Path(name).suffix not in (".md", ".rst", ".txt"):
            return False
        # --no-renames exposes both names as delete/add. Any deletion, type or
        # mode change, symlink, executable or submodule forces the full build.
        if status == b"A":
            if name in before:
                raise ValueError("invalid addition")
        elif status == b"M":
            if name not in before or not before[name].startswith(b"100644 blob "):
                return False
        else:
            return False
        if name not in after or not after[name].startswith(b"100644 blob "):
            return False
    return True


def eligible_skip(env):
    if env.get("GITHUB_EVENT_NAME") != "pull_request":
        return False
    base, head, source = (env.get(key, "") for key in ("PR_BASE_SHA", "PR_HEAD_SHA", "GITHUB_SHA"))
    if not all(SHA.fullmatch(value) for value in (base, head, source)):
        raise ValueError("missing or invalid commit identity")
    for commit in (base, head, source):
        if git("cat-file", "-t", commit) != b"commit\n":
            raise ValueError("not a commit")
    if git("rev-parse", "HEAD").decode("ascii").strip() != source:
        raise ValueError("checkout does not match test source")
    parents = git("rev-list", "--parents", "-n", "1", source).decode("ascii").split()
    if parents != [source, base, head]:
        raise ValueError("test source is not the expected PR merge")
    if git("status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("checkout is dirty")

    # Exact-byte pin protects the reviewed input closure, including future COPY
    # ., ADD, bind mounts, stages and other new ways of consuming the context.
    for commit in (base, head, source):
        if hashlib.sha256(git("show", f"{commit}:Dockerfile")).hexdigest() != DOCKERFILE_SHA256:
            return False
    before, proposed, tested = (tree(commit) for commit in (base, head, source))
    objects = {entry.split(b" ")[2]: entry.split(b" ")[1]
               for entries in (before, proposed, tested) for entry in entries.values()}
    # ls-tree can name absent blobs. Check availability without reading contents;
    # a partial object database is not proof that the image inputs are unchanged.
    records = git("cat-file", "--batch-check", input=b"\n".join(objects) + b"\n").splitlines()
    if len(records) != len(objects):
        raise ValueError("incomplete object check")
    for (oid, kind), record in zip(objects.items(), records):
        if not re.fullmatch(re.escape(oid + b" " + kind) + rb" [0-9]+", record):
            raise ValueError("missing or invalid tree object")
    return (
        docs_changes(base, head, before, proposed)
        and docs_changes(base, source, before, tested)
    )


def decision(env):
    try:
        return "skip" if eligible_skip(env) else "build"
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Image eligibility uncertain; requiring the full build.")
        return "build"


def gate(env):
    if env.get("PLAN_OUTCOME") != "success":
        raise ValueError("image planning did not succeed")
    planned = env.get("PLAN_DECISION")
    if planned == "build":
        if env.get("BUILDX_OUTCOME") != "success" or env.get("BUILD_OUTCOME") != "success":
            raise ValueError("required image build did not succeed")
        print("Required image build succeeded.")
    elif planned == "skip":
        if env.get("BUILDX_OUTCOME") != "skipped" or env.get("BUILD_OUTCOME") != "skipped":
            raise ValueError("unexpected image work outcome")
        # Recompute from Git and event identities in a separate process. The
        # planner's output alone is never evidence that a skip is eligible.
        if not eligible_skip(env):
            raise ValueError("image skip is not independently eligible")
        print("Eligible docs-only change: image work skipped (not executed).")
    else:
        raise ValueError("missing or invalid image decision")


def main():
    if sys.argv[1:] == ["plan"]:
        planned = decision(os.environ)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"decision={planned}\n")
        print(f"Image decision: {planned}")
    elif sys.argv[1:] == ["gate"]:
        gate(os.environ)
    else:
        raise ValueError("expected plan or gate")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not expose untrusted pathnames, process output or environment data.
        print("::error::Image decision/gate failed; Docker build is not validated.")
        sys.exit(1)
