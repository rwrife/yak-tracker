"""Interactive TUI for the yak-shaving forest (PLAN.md backlog #11, ``yak tui``).

The static ``rich`` render (``yak today``) is perfect for a glance, but a deep
day — every detour expanded — is a wall of text you have to scroll. This module
turns the same forest into an explorable `textual`_ app: collapse/expand any
node, cycle the narration format live, and read the one-paragraph summary in the
footer.

Design split, so the interesting part stays testable without a real terminal:

* :func:`build_tree_widget` is a **pure** function: forest → a populated
  ``textual`` :class:`~textual.widgets.Tree`. It has no side effects and needs no
  running event loop, so the headless test can assert on the widget shape
  (labels, nesting, node count) directly.
* :class:`YakTuiApp` is the thin `textual` :class:`~textual.app.App` shell that
  mounts that widget, wires key bindings, and (optionally) recomputes narration
  when the user cycles the format.

``textual`` is an **optional** dependency (``yak-tracker[tui]``); the core
install stays lean. Import errors are turned into a friendly message by
:func:`run_tui` so ``yak tui`` without the extra tells you exactly what to pip
install rather than blowing up with a traceback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from .config import VALID_FORMATS
from .tree import DetourKind, Node

__all__ = [
    "TuiUnavailableError",
    "ForestView",
    "node_label",
    "build_tree_widget",
    "run_tui",
]


class TuiUnavailableError(RuntimeError):
    """Raised when ``yak tui`` is invoked without the ``textual`` extra."""


# Plain-text glyph per detour kind, mirroring render.py's rich glyphs so the TUI
# reads the same way (rabbit hole vs. ordinary step) at a glance. Kept here as
# bare unicode (no rich markup) because textual's Tree renders labels directly.
_KIND_GLYPH: dict[str, str] = {
    DetourKind.ROOT: "\N{OX}",
    DetourKind.STEP: "\N{BULLET}",
    DetourKind.INSTALL: "\N{PACKAGE}",
    DetourKind.ERROR_FIX: "\N{FIRE}",
    DetourKind.DIR_CHANGE: "\N{OPEN FILE FOLDER}",
    DetourKind.BRANCH: "\N{TWISTED RIGHTWARDS ARROWS}",
}


@dataclass(frozen=True, slots=True)
class ForestView:
    """Everything the TUI needs to render one day.

    Bundling this keeps :func:`build_tree_widget` and the app decoupled from the
    collector/sessionizer pipeline: the CLI builds a ``ForestView`` and hands it
    over, so tests can construct one from a hand-built forest without touching
    shell history.

    Attributes:
        forest: One :class:`~yak_tracker.tree.Node` per session.
        day: The date this forest reconstructs (for the header).
        summaries: Per-format one-paragraph day summaries for the footer
            (narration or a deterministic fallback), keyed by persona. Missing
            or empty entries render as a friendly placeholder. Cycling the
            format (``f``) swaps between whichever personas are present here.
        fmt: The narration persona shown first.
    """

    forest: Sequence[Node]
    day: date | None = None
    summaries: Mapping[str, str] = field(default_factory=dict)
    fmt: str = "story"

    def summary_for(self, fmt: str) -> str:
        """Return the summary text for ``fmt`` (empty string if none)."""
        return (self.summaries.get(fmt) or "").strip()


def node_label(node: Node) -> str:
    """Plain-text label for a tree node: ``<glyph> <label> (HH:MM)``.

    Deterministic and rich-markup-free so it is safe both for textual's Tree and
    for a headless test to assert on exactly.
    """
    glyph = _KIND_GLYPH.get(node.kind, "\N{BULLET}")
    when = f" ({node.ts.strftime('%H:%M')})" if node.ts else ""
    return f"{glyph} {node.label}{when}"


def _attach(branch, node: Node) -> None:
    """Recursively add ``node``'s children under a textual Tree ``branch``."""
    for child in node.children:
        if child.children:
            sub = branch.add(node_label(child), expand=True)
            _attach(sub, child)
        else:
            branch.add_leaf(node_label(child))


def build_tree_widget(view: ForestView):
    """Build a populated textual :class:`~textual.widgets.Tree` for ``view``.

    Pure and side-effect-free: constructs the widget, roots it at a day header,
    adds one expandable branch per session (each a full intention→detour tree),
    and returns it. No event loop required — this is the seam the headless test
    drives.

    Raises:
        TuiUnavailableError: if ``textual`` is not installed.
    """
    try:
        from textual.widgets import Tree as TextualTree
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via run_tui
        raise TuiUnavailableError(
            "the TUI needs the 'textual' extra — install it with "
            "`pip install 'yak-tracker[tui]'`."
        ) from exc

    day_label = view.day.isoformat() if view.day is not None else "today"
    tree: TextualTree = TextualTree(f"\N{OX} yak — {day_label}")
    tree.root.expand()

    if not view.forest:
        tree.root.add_leaf("(no sessions for this day)")
        return tree

    for i, root in enumerate(view.forest, start=1):
        detours = root.descendants()
        depth = root.max_depth()
        header = (
            f"#{i} {node_label(root)}  "
            f"— {detours} event(s), {depth} level(s) deep"
        )
        branch = tree.root.add(header, expand=True)
        _attach(branch, root)

    return tree


def run_tui(view: ForestView, *, formats: Sequence[str] = VALID_FORMATS) -> None:
    """Launch the interactive TUI for ``view`` (blocks until the user quits).

    Imports ``textual`` lazily so importing this module (and running the rest of
    the CLI/tests) never requires the extra. If ``textual`` is missing, raises
    :class:`TuiUnavailableError` with an actionable install hint; the CLI turns
    that into a clean message instead of a traceback.
    """
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Footer, Header, Static
    except ModuleNotFoundError as exc:
        raise TuiUnavailableError(
            "the TUI needs the 'textual' extra — install it with "
            "`pip install 'yak-tracker[tui]'`."
        ) from exc

    cycle = [f for f in formats if f in VALID_FORMATS] or list(VALID_FORMATS)

    class YakTuiApp(App):
        """Explorable view of one day's yak-shaving forest."""

        CSS = """
        #summary {
            padding: 1 2;
            border-top: solid $panel;
            color: $text-muted;
        }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("f", "cycle_format", "Cycle format"),
            ("e", "expand_all", "Expand all"),
            ("c", "collapse_all", "Collapse all"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self._view = view
            self._fmt_index = (
                cycle.index(view.fmt) if view.fmt in cycle else 0
            )

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield build_tree_widget(self._view)
            yield Static(self._summary_text(), id="summary")
            yield Footer()

        def _summary_text(self) -> str:
            fmt = cycle[self._fmt_index]
            summary = self._view.summary_for(fmt) or "(no summary available)"
            return f"[{fmt}] {summary}"

        def _refresh_summary(self) -> None:
            self.query_one("#summary", Static).update(self._summary_text())

        def action_cycle_format(self) -> None:
            self._fmt_index = (self._fmt_index + 1) % len(cycle)
            self._refresh_summary()

        def action_expand_all(self) -> None:
            from textual.widgets import Tree as TextualTree

            self.query_one(TextualTree).root.expand_all()

        def action_collapse_all(self) -> None:
            from textual.widgets import Tree as TextualTree

            self.query_one(TextualTree).root.collapse_all()

    YakTuiApp().run()
