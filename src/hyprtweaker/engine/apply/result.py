"""What one Apply transaction concluded, as data rather than exceptions.

ADR-0010 names four outcomes -- ok, config errors, read-back mismatch, timeout -- because
those are the four the user-facing recovery branches on (ADR-0016). This module carries
those four plus the ones a transaction can reach *without* the compositor ever hearing
about it, and each extra earns its place by having a different answer:

* `NOTHING_TO_DO` -- the rendered bytes already matched what was on disk, so no reload was
  issued. A reload is a full teardown of the compositor's config state; spending one to
  reassert bytes that are already there is a visible stutter for nothing.
* `ABORTED` -- the transaction refused before touching disk: the `luac -p` gate rejected a
  rendered Module, a key was not in the Schema, or a caller aimed at a protected file.
  Always an app bug, never a user error, and the ADR's guarantee is precisely that *no
  file was replaced*.
* `WRITE_FAILED` -- the write started and the filesystem said no. Unlike `ABORTED`, the App
  dir may now be half-updated, which is a different conversation to have with the user.
* `COMPOSITOR_GONE` -- the Modules are on disk and durable, but nothing applied them: the
  socket died. Distinct from `TIMEOUT`, where the compositor is alive and the apply's fate
  is merely unknown.

Nothing here raises. A transaction runs on a background worker, and an exception there
would take the queue -- and therefore every later apply -- down with it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Final

from ..model import UNSET
from ..writer import WriteResult


class ApplyOutcome(enum.StrEnum):
    """The one thing that happened. Ordered roughly by how far the transaction got."""

    ABORTED = "aborted"
    """Refused before any file was replaced. Syntax gate, unknown key, protected path."""

    WRITE_FAILED = "write-failed"
    """The filesystem refused mid-write; the App dir may be partially updated."""

    NOTHING_TO_DO = "nothing-to-do"
    """Rendered bytes already on disk. No reload issued, and none was needed."""

    TIMEOUT = "timeout"
    """No `configreloaded` within the window. The apply's fate is unknown, not failed."""

    COMPOSITOR_GONE = "compositor-gone"
    """Written and durable, but never applied: the socket went away."""

    CONFIG_ERRORS = "config-errors"
    """The reload happened and Hyprland rejected something. `errors` holds its own lines."""

    READ_BACK_MISMATCH = "read-back-mismatch"
    """Clean parse, but the live values are not the ones the model asked for."""

    OK = "ok"
    """Written, reloaded, no errors, and every checked key reads back as asked."""


class _Unreadable(enum.Enum):
    """Sentinel type, so `UNREADABLE` narrows under mypy and stays distinct from `None`.

    `None` is already a value here -- an explicitly-null Option -- so "the live value could
    not be read at all" needs an object of its own.
    """

    TOKEN = enum.auto()

    def __repr__(self) -> str:
        return "UNREADABLE"


UNREADABLE: Final = _Unreadable.TOKEN
"""`getoption` answered, but not with anything this Option's parser could read."""


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One key whose live value is not what the model asked for.

    Carries both sides because the recovery is a user-facing message ("rounding is 8, not
    the 10 you set") and the Row's drift badge wants the live value to show (ADR-0005).
    """

    name: str
    expected: Any
    """The model's value: a typed value, `None` for explicit null, or `UNSET`."""

    actual: Any
    """The live value, or `UNREADABLE`."""

    live_set: bool
    """Whether the running config sets this Option at all."""

    @property
    def unapplied(self) -> bool:
        """The model sets this key and the live config does not -- the Module never ran.

        The loud shape of mismatch, and worth a name: a `require` that failed, or a Module
        the Writer stood down from because an editor had touched it. A plain value
        disagreement is the quiet shape, and usually means `user.lua` or a Bridge won the
        override order on purpose.
        """
        return not self.live_set and self.expected is not UNSET


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The structured outcome ADR-0010 promises and error surfacing (#60) consumes."""

    outcome: ApplyOutcome
    keys: tuple[str, ...] = ()
    """The Option names this transaction was asked to confirm, in the order given."""

    write: WriteResult | None = None
    """What the Writer did, verbatim. `None` when the transaction never got that far.

    Held whole rather than copied field by field: a `WriteResult` already answers written /
    unchanged / removed / skipped / hand-edited, and a second hand-maintained copy of that
    vocabulary is one release away from disagreeing with the first.
    """

    errors: tuple[str, ...] = ()
    """`configerrors` lines verbatim, `file:line`-prefixed as Hyprland wrote them.

    The prefix is load-bearing: ADR-0016 attributes ownership by it, and reformatting the
    line here would throw away the only evidence of *whose* file failed.
    """

    mismatches: tuple[Mismatch, ...] = field(default=())
    unconfirmed: tuple[str, ...] = ()
    """Keys Read-back could not settle either way -- not agreed, and not disagreed.

    A reply the app cannot read is not evidence that the value is wrong. Hyprland 0.56.2
    answers `getoption` for both font-weight Options with `invalid type (internal error)`
    (upstream bug, recorded against #51), and a key whose name the running compositor does
    not know at all answers `no such option` -- neither says anything about what the config
    now holds.

    Kept apart from `mismatches` because the two have opposite recoveries: ADR-0016 wires a
    mismatch to auto-revert, so filing "could not read" under it would undo correct writes.
    An unconfirmed key leaves the outcome `OK` and gives #60 something honest to badge.
    """

    pending_restart: tuple[str, ...] = ()
    """Restart-flagged keys this transaction wrote: on file, effective after a restart."""

    detail: str = ""
    """Human-readable "why", for the outcomes whose cause is an exception message."""

    @property
    def ok(self) -> bool:
        """The model and the live compositor agree, or there was nothing to make agree."""
        return self.outcome in (ApplyOutcome.OK, ApplyOutcome.NOTHING_TO_DO)

    @property
    def reached_disk(self) -> bool:
        """Whether any file was replaced. `False` is ADR-0010's syntax-gate guarantee."""
        return self.write is not None and self.write.changed

    @property
    def written(self) -> tuple[str, ...]:
        return self.write.written if self.write is not None else ()

    @property
    def skipped(self) -> tuple[str, ...]:
        """App-owned files left alone because an editor got to them first (ADR-0005)."""
        return self.write.skipped if self.write is not None else ()
