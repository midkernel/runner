from midkernel_runner.mode import (
    IMAGE_KIMI_BIN,
    in_graph_mode,
    should_clone_target,
    should_require_report,
)


def test_image_kimi_bin_is_real_binary():
    assert IMAGE_KIMI_BIN == "/opt/midkernel/kimi.bin"


def test_in_graph_mode():
    assert in_graph_mode({"MIDKERNEL_AGENTFLOW_TARGET": "local"}) is True
    assert in_graph_mode({"MIDKERNEL_AGENTFLOW_TARGET": "in-task"}) is True
    assert in_graph_mode({"MIDKERNEL_AGENTFLOW_TARGET": "ecs"}) is False
    assert in_graph_mode({}) is False


def test_should_clone_target_false_in_graph_mode():
    assert should_clone_target({"MIDKERNEL_AGENTFLOW_TARGET": "local"}) is False
    assert should_clone_target({"MIDKERNEL_CLONE_TARGET": "0"}) is False
    assert should_clone_target({"MIDKERNEL_CLONE_TARGET": "1", "MIDKERNEL_AGENTFLOW_TARGET": "local"}) is True
    assert should_clone_target({}) is True


def test_should_require_report_false_for_intermediate_graph_nodes():
    run = {"RUN_ID": "cmtuavpvs0003ib04bfyr7roc"}
    assert should_require_report({**run, "MIDKERNEL_AGENTFLOW_TARGET": "local"}) is False
    assert should_require_report({**run, "MIDKERNEL_REQUIRE_REPORT": "0"}) is False
    assert should_require_report({**run, "MIDKERNEL_SKIP_REPORT_PUBLISH": "1"}) is False
    assert should_require_report(run) is True
    assert should_require_report({}) is False
