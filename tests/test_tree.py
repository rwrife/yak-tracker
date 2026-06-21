"""M4 tests for the yak-shaving tree builder (intention → detour nesting)."""

from __future__ import annotations

from datetime import datetime, timedelta

from yak_tracker.models import Event
from yak_tracker.sessionize import Session
from yak_tracker.tree import (
    DetourKind,
    Node,
    build_forest,
    build_tree,
    classify_event,
)

BASE = datetime(2025, 6, 17, 9, 0, 0)


def _ev(
    minutes: float,
    cmd: str,
    *,
    source: str = "shell:zsh",
    cwd: str | None = None,
) -> Event:
    """Event at ``BASE + minutes`` with the given command/source/cwd."""
    return Event(cmd=cmd, ts=BASE + timedelta(minutes=minutes), cwd=cwd, source=source)


def _session(*events: Event) -> Session:
    return Session(events=list(events))


# --- classify_event ---------------------------------------------------------


def test_classify_install():
    kind, label = classify_event(_ev(0, "npm install left-pad"), current_repo=None)
    assert kind == DetourKind.INSTALL
    assert "npm install" in label


def test_classify_install_variants():
    for cmd in [
        "pip install requests",
        "uv add httpx",
        "cargo add serde",
        "brew install jq",
        "yarn add react",
        "poetry lock",
    ]:
        kind, _ = classify_event(_ev(0, cmd), current_repo=None)
        assert kind == DetourKind.INSTALL, cmd


def test_classify_error_fix():
    for cmd in [
        "rm -rf node_modules",
        "git reset --hard HEAD~1",
        "git clean -xdf",
        "git push --force",
    ]:
        kind, _ = classify_event(_ev(0, cmd), current_repo=None)
        assert kind == DetourKind.ERROR_FIX, cmd


def test_classify_dir_change_via_cd():
    kind, label = classify_event(_ev(0, "cd ../sibling"), current_repo=None)
    assert kind == DetourKind.DIR_CHANGE
    assert label == "cd ../sibling"


def test_classify_dir_change_via_repo_switch():
    ev = _ev(0, "commit abc123 fix things", source="git:other", cwd="/repos/other")
    kind, _ = classify_event(ev, current_repo="main-repo")
    assert kind == DetourKind.DIR_CHANGE


def test_classify_branch_switch():
    ev = _ev(0, "reflog 1a2b3c checkout: moving from main to fix-bug", source="git:r")
    kind, label = classify_event(ev, current_repo="r")
    assert kind == DetourKind.BRANCH
    assert "main" in label and "fix-bug" in label


def test_classify_plain_step():
    kind, label = classify_event(_ev(0, "python app.py"), current_repo=None)
    assert kind == DetourKind.STEP
    assert label == "python app.py"


# --- root intention ---------------------------------------------------------


def test_root_prefers_first_commit_subject():
    s = _session(
        _ev(0, "ls"),
        _ev(1, "commit deadbee Add login form", source="git:app", cwd="/app"),
        _ev(2, "git push"),
    )
    root = build_tree(s)
    assert root.kind == DetourKind.ROOT
    assert root.label == "Add login form"


def test_root_skips_trivial_commands():
    s = _session(_ev(0, "ls"), _ev(1, "cd src"), _ev(2, "vim main.py"))
    root = build_tree(s)
    # ls/cd are trivial → first substantive command becomes the root.
    assert root.label == "vim main.py"


def test_root_falls_back_to_first_event_when_all_trivial():
    s = _session(_ev(0, "ls"), _ev(1, "pwd"))
    root = build_tree(s)
    assert root.label == "ls"


def test_empty_session_root():
    root = build_tree(Session(events=[]))
    assert root.kind == DetourKind.ROOT
    assert root.label == "session"
    assert root.children == []


# --- tree shape -------------------------------------------------------------


def test_steps_hang_off_root():
    s = _session(
        _ev(0, "vim app.py"),
        _ev(1, "python app.py"),
        _ev(2, "pytest"),
    )
    root = build_tree(s)
    # root = vim; the other two are plain steps under it
    assert root.label == "vim app.py"
    assert [c.label for c in root.children] == ["python app.py", "pytest"]
    assert all(c.kind == DetourKind.STEP for c in root.children)


def test_detour_becomes_child_of_root():
    s = _session(
        _ev(0, "vim app.py"),
        _ev(1, "npm install left-pad"),
        _ev(2, "python app.py"),
    )
    root = build_tree(s)
    kinds = [c.kind for c in root.children]
    assert DetourKind.INSTALL in kinds
    # the step after the detour pops back under the install (active detour)
    install = next(c for c in root.children if c.kind == DetourKind.INSTALL)
    assert any(g.label == "python app.py" for g in install.children)


def test_same_kind_detours_nest_deeper():
    # classic spiral: three installs in a row nest progressively
    s = _session(
        _ev(0, "vim app.py"),
        _ev(1, "pip install a"),
        _ev(2, "pip install b"),
        _ev(3, "pip install c"),
    )
    root = build_tree(s)
    assert root.max_depth() == 3  # root → a → b → c
    # walk the single nested chain
    node = root
    for expected in ["pip install a", "pip install b", "pip install c"]:
        assert len(node.children) == 1
        node = node.children[0]
        assert node.label.startswith(expected.split()[0])


def test_different_kind_detour_pops_to_root():
    s = _session(
        _ev(0, "vim app.py"),
        _ev(1, "pip install a"),
        _ev(2, "rm -rf node_modules"),  # different kind → new branch off root
    )
    root = build_tree(s)
    assert len(root.children) == 2
    kinds = {c.kind for c in root.children}
    assert kinds == {DetourKind.INSTALL, DetourKind.ERROR_FIX}


def test_dir_change_then_step_nests_under_dir():
    s = _session(
        _ev(0, "vim app.py"),
        _ev(1, "cd ../other"),
        _ev(2, "cargo build"),
    )
    root = build_tree(s)
    dir_node = next(c for c in root.children if c.kind == DetourKind.DIR_CHANGE)
    assert any(g.label == "cargo build" for g in dir_node.children)


# --- Node helpers -----------------------------------------------------------


def test_node_descendants_and_depth():
    root = Node(label="root", kind=DetourKind.ROOT)
    a = root.add(Node(label="a", kind=DetourKind.STEP))
    a.add(Node(label="b", kind=DetourKind.STEP))
    root.add(Node(label="c", kind=DetourKind.STEP))
    assert root.descendants() == 3
    assert root.max_depth() == 2


def test_build_forest_one_tree_per_session():
    s1 = _session(_ev(0, "vim a"))
    s2 = _session(_ev(0, "vim b"))
    forest = build_forest([s1, s2])
    assert len(forest) == 2
    assert [n.label for n in forest] == ["vim a", "vim b"]
