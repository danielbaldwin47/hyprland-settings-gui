"""The recovery matrix: which recovery each `configerrors` line has earned (ADR-0016).

`ownership.py` answers *whose file failed*. This answers *what may be done about it*, and
the two are deliberately separate modules: attribution is a parse and has one right answer,
while recovery is policy and is the thing this ADR exists to decide. Keeping the policy pure
-- lines in, a plan out, no writes and no sockets -- is what makes ADR-0016's table testable
as a table rather than as a sequence of UI clicks.

The whole of the decision is `_ACTIONS` below, one row per Ownership class, and it is
readable beside the ADR's own table. Everything else here exists to serve it:

* errors are **grouped by file**, because recovery is per file and a Module with six syntax
  errors is one problem with one Restore button, not six;
* a `Problem` carries the **first line number** it saw, because "Open file" is only worth
  offering if it can land the editor on the line;
* the plan carries the **emergency** flag separately, because zero binds does not change
  *which* actions exist -- it changes whether the app may take one without asking.

**No action here writes anything.** `Action` is a vocabulary the session acts on and the
dialog renders; a module that could both decide and perform would make "what would this
error offer?" a question you could only answer by letting it happen.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from .ownership import ConfigError, Ownership, attribute


class Action(enum.StrEnum):
    """One recovery the user may be offered. ADR-0016's per-class action buttons."""

    AUTO_REVERT = "auto-revert"
    """Not a button: the app has already done it, or is about to (ADR-0016 §Auto-revert).

    In the vocabulary anyway, so the matrix is total. A class whose row was simply absent
    would read as "nothing can be done here", which for the one class the app recovers from
    automatically is precisely backwards -- and `Session` asserts against this row rather
    than against a second copy of the rule.
    """

    RESTORE_LAST_GOOD = "restore-last-good"
    """Put this Module back to the newest bytes a confirmed transaction left (§Last known
    good). Offered, never taken automatically -- except when the user is stranded."""

    OPEN_FILE = "open-file"
    """Open the file in the user's editor, at the line if there is one. The only recovery
    the app can offer for a file it must not write."""

    QUARANTINE = "quarantine"
    """Regenerate the Entrypoint without this file's `require`, with consent, reversibly."""

    REGENERATE = "regenerate"
    """Rewrite the Entrypoint from scratch. Always available for it and nothing else: it is
    app-owned and derived entirely from the Module set, so there is never anything in it
    worth preserving that the app cannot produce again."""


_ACTIONS: dict[Ownership, tuple[Action, ...]] = {
    Ownership.OWN_WRITE: (Action.AUTO_REVERT,),
    Ownership.APP_MODULE: (Action.RESTORE_LAST_GOOD, Action.OPEN_FILE),
    Ownership.FOREIGN: (Action.OPEN_FILE, Action.QUARANTINE),
    Ownership.ENTRYPOINT: (Action.REGENERATE, Action.OPEN_FILE),
    Ownership.UNKNOWN: (),
}
"""ADR-0016's recovery table, transcribed.

The two rows worth defending, because both are the ADR refusing an obvious-looking option:

* `APP_MODULE` does **not** include auto-revert. The app owns the file but did not write
  what is in it -- a hand edit, or a write from an older session -- and rewriting it would
  destroy user intent to fix an error the user may have introduced deliberately.
* `UNKNOWN` offers **nothing**. A line with no file in it, or a file in nobody's territory,
  is shown verbatim and left alone; picking the closest-looking class would authorise a
  write to a file the app was never asked to touch.
"""


@dataclass(frozen=True, slots=True)
class Problem:
    """Every error blaming one file, with the recovery its Ownership class earns.

    Per file rather than per line: a Module with six syntax errors is one broken file with
    one Restore button, and a dialog offering six identical buttons would be asking the user
    to pick between them.
    """

    path: str
    """The file as Hyprland named it, or the `require` target. `""` when the line had no
    file in it at all -- the `UNKNOWN` shape, which has nothing to open."""

    module: str | None
    """The App-dir-relative name this file is, or `None` for a file the app does not own."""

    ownership: Ownership
    errors: tuple[ConfigError, ...]
    """Every line blaming this file, in the order Hyprland reported them."""

    actions: tuple[Action, ...]

    @property
    def line(self) -> int | None:
        """The first line number any error gave for this file, for Open-at-line.

        The first rather than the lowest: Hyprland reports in the order it hit them, so the
        first is the one that stopped the parse and the one worth landing the editor on.
        """
        for error in self.errors:
            if error.number is not None:
                return error.number
        return None

    @property
    def lines(self) -> tuple[str, ...]:
        """The raw `configerrors` lines, prefixes intact, for the dialog's monospace list."""
        return tuple(error.line for error in self.errors)

    def offers(self, action: Action) -> bool:
        return action in self.actions


@dataclass(frozen=True, slots=True)
class Recovery:
    """What one unhealthy reload means and what may be done about it.

    The whole of ADR-0016's policy for a set of `configerrors` lines, as data. `Session`
    performs it and the Banner renders it; neither re-derives any part of it.
    """

    problems: tuple[Problem, ...] = ()
    """One per blamed file, in the order the files were first mentioned."""

    stranded: bool = False
    """Config errors and zero binds -- Hyprland's emergency mode (ADR-0016 §Zero-binds).

    The flag that suspends the consent gate. Named for the user's situation rather than for
    the probe ("zero_binds") because it is the situation that justifies the override: without
    binds they may not be able to open a terminal to fix anything themselves.
    """

    @property
    def unhealthy(self) -> bool:
        """Whether anything is wrong at all -- the Banner's own condition."""
        return bool(self.problems)

    @property
    def entrypoint_refused(self) -> bool:
        """Whether the Entrypoint itself failed -- the red-Banner case.

        Worth its own question because it is the one state where the *previous* config is
        still live: Hyprland refuses the whole file in phase 1 and keeps running what it
        had, so the user's session looks fine while the files on disk are broken. A Banner
        that said only "there are config errors" would understate that badly.
        """
        return any(problem.ownership is Ownership.ENTRYPOINT for problem in self.problems)

    @property
    def auto_restorable(self) -> tuple[str, ...]:
        """App-owned Modules the emergency restore may put back without asking.

        Empty unless `stranded`, which is the consent gate stated as code. Scoped to Modules
        the app owns and to `RESTORE_LAST_GOOD` -- being stranded justifies overriding the
        hand-edit gate on the app's *own* files, never touching somebody else's. When the
        error is in a foreign file this is empty and the Banner says so plainly, which is
        exactly what the ADR asks for.
        """
        if not self.stranded:
            return ()
        return tuple(
            problem.module
            for problem in self.problems
            if problem.module is not None and problem.offers(Action.RESTORE_LAST_GOOD)
        )


def plan(
    errors: Sequence[str],
    *,
    written: Sequence[str] = (),
    binds: int | None = None,
) -> Recovery:
    """Attribute `errors` and route each blamed file to its recovery.

    `written` names what the current transaction laid down, and is passed straight through
    to attribution: it is the only evidence that separates `OWN_WRITE` -- the one class that
    authorises an automatic write -- from a file the app merely happens to own.

    `binds` is the post-reload probe, and `None` means it was not taken. Only an explicit
    zero strands the user; conflating "not asked" with "none" would fire the emergency
    restore on every transaction that never probed.
    """
    attributed = attribute(errors, written=written)
    return Recovery(problems=_group(attributed), stranded=bool(attributed) and binds == 0)


def _group(errors: Sequence[ConfigError]) -> tuple[Problem, ...]:
    """One `Problem` per blamed file, in the order the files were first mentioned.

    Keyed by `(path, ownership)` rather than by path alone. The pair cannot normally
    disagree for one path, and keying on it costs nothing -- but a path that somehow
    attributed two ways would otherwise have its lines merged under whichever class was seen
    first, silently offering one file's recovery for another file's error.
    """
    order: list[tuple[str, Ownership]] = []
    grouped: dict[tuple[str, Ownership], list[ConfigError]] = {}
    for error in errors:
        key = (error.path, error.ownership)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(error)

    return tuple(
        Problem(
            path=path,
            module=grouped[(path, ownership)][0].module,
            ownership=ownership,
            errors=tuple(grouped[(path, ownership)]),
            actions=_ACTIONS[ownership],
        )
        for path, ownership in order
    )
