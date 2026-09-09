import os

from midkernel_runner.config import load_config
from midkernel_runner.main import run
from midkernel_runner.node import PreparedNode
from midkernel_runner.secrets import HarnessSecrets


def test_run_graph_path_pins_flags_before_prepare(tmp_path, monkeypatch):
    """Flags must be on os.environ before prepare_node (entrypoint already skipped)."""
    work = tmp_path / "workspace"
    work.mkdir()
    (work / ".midkernel").mkdir()
    seen = {}

    def fake_prepare(*, clone_target=None, environ=None):
        seen["clone_target"] = clone_target
        seen["MIDKERNEL_CLONE_TARGET"] = os.environ.get("MIDKERNEL_CLONE_TARGET")
        seen["MIDKERNEL_AGENTFLOW_TARGET"] = os.environ.get("MIDKERNEL_AGENTFLOW_TARGET")
        seen["MIDKERNEL_REQUIRE_REPORT"] = os.environ.get("MIDKERNEL_REQUIRE_REPORT")
        return PreparedNode(
            secrets=HarnessSecrets("sk-or-v1-x", "ghs_x"),
            config=None,
            kimi_config_path=None,
            cloned=False,
        )

    monkeypatch.setenv("RUN_ID", "cmtue7rv90003l104ysmh21eu")
    monkeypatch.setenv("GITHUB_OWNER", "acme")
    monkeypatch.setenv("GITHUB_NAME", "target")
    monkeypatch.setenv("PLAYBOOK", "goal-security-review")
    monkeypatch.setenv("WORKDIR", str(work))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("MIDKERNEL_LOCAL", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_x")
    monkeypatch.delenv("MIDKERNEL_CLONE_TARGET", raising=False)
    monkeypatch.delenv("MIDKERNEL_AGENTFLOW_TARGET", raising=False)
    monkeypatch.delenv("MIDKERNEL_REQUIRE_REPORT", raising=False)

    monkeypatch.setattr("midkernel_runner.main.should_run_playbooks_graph", lambda _cfg: True)
    monkeypatch.setattr("midkernel_runner.main.prepare_node", fake_prepare)
    monkeypatch.setattr("midkernel_runner.main.run_playbooks_graph", lambda _cfg: 0)

    assert run() == 0
    assert seen["clone_target"] is False
    assert seen["MIDKERNEL_CLONE_TARGET"] == "0"
    assert seen["MIDKERNEL_AGENTFLOW_TARGET"] == "local"
    assert seen["MIDKERNEL_REQUIRE_REPORT"] == "0"
    # load_config is exercised so the env is a real RunConfig shape
    assert load_config().playbook_slug == "goal-security-review"
