from midkernel_runner.concurrency import (
    CONCURRENCY_ENV_KEYS,
    DEFAULT_GOAL_CONCURRENCY,
    GOAL_CONCURRENCY_PICKER,
    MAX_GRAPH_CONCURRENCY,
    apply_graph_concurrency_env,
    concurrency_env,
    parse_graph_concurrency,
    resolve_graph_concurrency,
)


def test_playbooks_17_contract():
    assert CONCURRENCY_ENV_KEYS == (
        "GOAL_CONCURRENCY",
        "CONCURRENCY",
        "GRAPH_CONCURRENCY",
    )
    assert DEFAULT_GOAL_CONCURRENCY == 2
    assert GOAL_CONCURRENCY_PICKER == (1, 2, 4, 6)
    assert MAX_GRAPH_CONCURRENCY == 6


def test_parse_accepts_picker_and_1_to_6():
    assert parse_graph_concurrency(None) is None
    assert parse_graph_concurrency("") is None
    assert parse_graph_concurrency("nope") is None
    assert parse_graph_concurrency("0") is None
    assert parse_graph_concurrency("1") == 1
    assert parse_graph_concurrency("2") == 2
    assert parse_graph_concurrency("3") == 3
    assert parse_graph_concurrency("4") == 4
    assert parse_graph_concurrency("6") == 6
    assert parse_graph_concurrency("99") == 6


def test_resolve_first_wins_goal_concurrency():
    assert resolve_graph_concurrency({}) is None
    assert resolve_graph_concurrency({"GOAL_CONCURRENCY": "4", "CONCURRENCY": "1"}) == 4
    assert resolve_graph_concurrency({"CONCURRENCY": "6"}) == 6
    assert resolve_graph_concurrency({"GRAPH_CONCURRENCY": "1"}) == 1
    assert resolve_graph_concurrency({"AGENTFLOW_CONCURRENCY": "2"}) == 2
    assert resolve_graph_concurrency({"MIDKERNEL_CONCURRENCY": "4"}) == 4


def test_apply_does_not_invent_playbooks_default():
    env = apply_graph_concurrency_env({"GOAL_COUNT": "6"})
    assert "GOAL_CONCURRENCY" not in env
    assert "CONCURRENCY" not in env


def test_apply_writes_playbooks_emit_names():
    env = apply_graph_concurrency_env({"CONCURRENCY": "4", "GOAL_COUNT": "6"})
    assert env["GOAL_CONCURRENCY"] == "4"
    assert env["CONCURRENCY"] == "4"
    assert env["GRAPH_CONCURRENCY"] == "4"
    aliases = concurrency_env(2)
    assert tuple(aliases) == CONCURRENCY_ENV_KEYS
    assert aliases["GOAL_CONCURRENCY"] == "2"


def test_apply_goal_concurrency_from_task_env():
    env = apply_graph_concurrency_env({"GOAL_CONCURRENCY": "2"})
    assert env["GOAL_CONCURRENCY"] == "2"
    assert env["CONCURRENCY"] == "2"
    assert env["GRAPH_CONCURRENCY"] == "2"
