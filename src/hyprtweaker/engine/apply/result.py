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
import logging
from dataclasses import dataclass, field
from typing import Any, Final

from ..model import UNSET, parse_getoption
from ..schema import ResolvedOption
from ..writer import WriteResult

_log = logging.getLogger(__name__)


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


def live_value(option: ResolvedOption, payload: dict[str, Any]) -> Any:
    """One `getoption` reply as a model value, or `UNREADABLE` if the parser refused it.

    Lives beside the sentinel because it is the only thing that produces one, and it is one
    function rather than two so that Read-back and the full state re-read cannot drift into
    disagreeing about what "unreadable" means. Refusing is a Hyprland-version surprise, not
    evidence about the value: what each caller does with that is its own business, but the
    judgement is made once.
    """
    try:
        return parse_getoption(option, payload)
    except (KeyError, ValueError, TypeError) as error:
        _log.warning("unreadable getoption reply for %s: %s", option.name, error)
        return UNREADABLE


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
    """The structured outcome ADR-0010 promises and `recovery.py` branches on."""

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

    Kept apart from `mismatches` because filing "could not read" as "did not apply" tells
    the user a falsehood: ADR-0016 badges an unexplained mismatch on the Row as "didn't
    apply", which for a font-weight write would be wrong every single time. It also keeps
    `confirmed` honest -- see there.

    The same word ADR-0016 uses for a timed-out transaction ("re-poll once; if still
    unconfirmed"), in the same spirit but per key rather than per transaction.
    """

    pending_restart: tuple[str, ...] = ()
    """Restart-flagged keys this transaction wrote: on file, effective after a restart."""

    binds: int | None = None
    """How many keybinds the live config declared after this reload, if it was asked.

    Only probed when the reload reported errors, so `None` means "not asked" on every clean
    apply rather than "none". The two must not be conflated: ADR-0016's emergency restore
    fires on *zero*, and treating an unasked probe as zero would have a clean transaction
    trigger an emergency restore past the user's consent gate.
    """

    detail: str = ""
    """Human-readable "why", for the outcomes whose cause is an exception message."""

    @property
    def stranded(self) -> bool:
        """Config errors *and* no keybinds: Hyprland's emergency mode (ADR-0016).

        The one condition that lets the app overwrite a hand edit without asking. Both
        halves are required -- errors alone is an ordinary unhealthy state, and a config
        that genuinely declares no binds is a legitimate (if unusual) choice that nothing
        went wrong in.
        """
        return self.outcome is ApplyOutcome.CONFIG_ERRORS and self.binds == 0

    @property
    def ok(self) -> bool:
        """Nothing is known to have gone wrong -- so there is nothing to tell the user.

        Not the same as "verified": a transaction with `unconfirmed` keys is `ok`, because
        no error was reported and no value was found to disagree. `confirmed` is the
        stricter question, and the one to ask before treating a write as good.
        """
        return self.outcome in (ApplyOutcome.OK, ApplyOutcome.NOTHING_TO_DO)

    @property
    def confirmed(self) -> bool:
        """Every key this transaction wrote was actually checked against the compositor.

        ADR-0016's Last-known-good gate: "the newest Journal Snapshot whose transaction
        confirmed clean (empty `configerrors` + read-back ok). Journal entries gain a
        `confirmed` flag written after Read-back." This is that flag, and the Journal records
        it per transaction.

        Stricter than `ok` on purpose. A transaction that could not read half its keys back
        has verified nothing about them, and promoting its Snapshot to last-known-good would
        make the app's idea of "good" a state nobody ever checked -- which is exactly what
        restore-last-good would then restore the user to.

        Restart-flagged keys do not count against it: they are skipped by design, not by
        failure, and a config made entirely of them would otherwise never establish a
        last-known-good at all.
        """
        return self.outcome is ApplyOutcome.OK and not self.unconfirmed

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
