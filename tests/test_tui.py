"""Headless tests for the ``yak tui`` widget construction (issue #31).

The interactive app needs a real terminal, but the *interesting* logic — turning
a yak-shaving forest into a populated ``textual`` Tree — is a pure function
(:func:`~yak_tracker.tui.build_tree_widget`). These tests drive that seam with
hand-built forests, no event loop and no TTY required, so CI can assert on the
widget's shape (labels, nesting, node count) directly.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from yak_tracker.models import Event
from yak_tracker.tree import DetourKind, Node
from yak_tracker.tui import (
    ForestView,
    build_tree_widget,
    node_label,
)

# Skip the whole module cleanly if the optional extra isn't installed, so a lean
# (no-textual) checkout still collects the rest of the suite.
pytest.importorskip("textual")


def _ev(cmd: str, hour: int) -> Event:
    return Event(cmd=cmd, ts=datetime(2026, 7, 13, hour, 0), cwd=None, source="shell:zsh")


def _sample_forest() -> list[Node]:
    """A tiny two-session forest with one nested install detour."""
    root1 = Node(
        label="fix login bug",
        kind=DetourKind.ROOT,
        event=_ev("commit abc fix login", 9),
    )
    detour = Node(
        label="npm install left-pad",
        kind=DetourKind.INSTALL,
        event=_ev("npm install left-pad", 9),
    )
    detour.add(
        Node(
            label="rm -rf node_modules",
            kind=DetourKind.ERROR_FIX,
            event=_ev("rm -rf node_modules", 10),
        )
    )
    root1.add(detour)
    root1.add(Node(label="run tests", kind=DetourKind.STEP, event=_ev("pytest", 10)))

    root2 = Node(label="write docs", kind=DetourKind.ROOT, event=_ev("vim README.md", 14))
    return [root1, root2]


def _walk(node):
    """Yield a textual TreeNode and all its descendants."""
    yield node
    for child in node.children:
        yield from _walk(child)


def test_node_label_has_glyph_time_and_text():
    node = Node(label="fix login bug", kind=DetourKind.ROOT, event=_ev("commit", 9))
    label = node_label(node)
    assert "fix login bug" in label
    assert "(09:00)" in label
    assert label.startswith("\N{OX}")


def test_node_label_without_timestamp_omits_time():
    node = Node(label="mystery", kind=DetourKind.STEP, event=None)
    label = node_label(node)
    assert "mystery" in label
    assert "(" not in label


def test_build_tree_widget_mirrors_forest_structure():
    view = ForestView(forest=_sample_forest(), day=date(2026, 7, 13))
    tree = build_tree_widget(view)

    # Root header carries the date.
    assert "2026-07-13" in str(tree.root.label)

    # One branch per session.
    assert len(tree.root.children) == 2

    # Every node label from the forest shows up somewhere in the widget.
    labels = " || ".join(str(n.label) for n in _walk(tree.root))
    for expected in [
        "fix login bug",
        "npm install left-pad",
        "rm -rf node_modules",
        "run tests",
        "write docs",
    ]:
        assert expected in labels

    # Session header advertises the detour count + depth.
    first_header = str(tree.root.children[0].label)
    assert "event(s)" in first_header and "level(s) deep" in first_header

    # The nested error-fix lives *under* the install detour, not at the top.
    install_branch = None
    for branch in _walk(tree.root):
        if "npm install left-pad" in str(branch.label):
            install_branch = branch
            break
    assert install_branch is not None
    nested = " ".join(str(c.label) for c in install_branch.children)
    assert "rm -rf node_modules" in nested


def test_build_tree_widget_empty_forest_is_friendly():
    view = ForestView(forest=[], day=date(2026, 7, 13))
    tree = build_tree_widget(view)
    assert len(tree.root.children) == 1
    assert "no sessions" in str(tree.root.children[0].label).lower()


def test_forest_view_summary_lookup():
    view = ForestView(
        forest=_sample_forest(),
        day=date(2026, 7, 13),
        summaries={"story": "  a wild saga  ", "standup": ""},
        fmt="story",
    )
    assert view.summary_for("story") == "a wild saga"
    assert view.summary_for("standup") == ""
    assert view.summary_for("learning") == ""  # missing key
