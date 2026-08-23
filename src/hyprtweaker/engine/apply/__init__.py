"""Apply: the serialized live-apply pipeline (ADR-0010).

One user-visible change is one **Apply transaction**: render every dirty Module whole,
syntax-gate the lot, atomic-rename them, ask for exactly one `reload`, then confirm by
reading `configerrors` and the touched keys back. One transaction runs at a time and edits
arriving meanwhile coalesce into the next, because a reload is a full teardown of the
compositor's config state and `configerrors` is one global slot that parallel applies could
not attribute.

Four modules, in dependency order:

- `result.py` -- `ApplyOutcome`, `ApplyResult`, `Mismatch`: the answer #60 branches on;
- `transaction.py` -- `ApplyTransaction`, the five steps and the in-flight flag;
- `queue.py` -- `ApplyQueue`, debounce and serialization, with no idea sockets exist;
- `foreign.py` -- `ForeignReloadWatch`, the "somebody else reloaded" signal;
- `preview.py` -- `EvalPreview`, the transient per-tick tier a continuous gesture uses;
- `reread.py` -- `read_state`, the full state re-read that answers a foreign reload and
  recovers the model at startup;
- `applier.py` -- `Applier`, the three wired together, which is what the app holds.

Typical use::

    applier = Applier(model=model, writer=writer, client=client, events=events,
                      on_result=window.show_apply_result)
    model.set("decoration:rounding", 10)
    result = await applier.apply("decoration:rounding")
    result.outcome          # ApplyOutcome.OK
    result.pending_restart  # keys that need a Hyprland restart to take effect

The Eval preview tier is a second, transient apply path for continuous widgets, and it must
never run between a reload and its Read-back -- `eval` clears `configerrors`. `Applier` is
what enforces that: `EvalPreview` asks its queue whether a transaction is anywhere in
progress before every send, so the ordering cannot be got wrong from outside::

    applier.preview("general:col.active_border")   # a drag tick: eval, no file touched
    applier.commit("general:col.active_border")    # the release: one Apply transaction
"""

from __future__ import annotations

from .applier import Applier
from .foreign import ForeignReloadWatch
from .preview import EvalPreview, preview_code
from .queue import DEBOUNCE_SECONDS, ApplyQueue, Transaction
from .reread import ReRead, app_owned_options, read_state
from .result import UNREADABLE, ApplyOutcome, ApplyResult, Mismatch
from .transaction import (
    RELOAD_TIMEOUT_SECONDS,
    SETTLE_SECONDS,
    ApplyTransaction,
)

__all__ = [
    "DEBOUNCE_SECONDS",
    "RELOAD_TIMEOUT_SECONDS",
    "SETTLE_SECONDS",
    "UNREADABLE",
    "Applier",
    "ApplyOutcome",
    "ApplyQueue",
    "ApplyResult",
    "ApplyTransaction",
    "EvalPreview",
    "ForeignReloadWatch",
    "Mismatch",
    "ReRead",
    "Transaction",
    "app_owned_options",
    "preview_code",
    "read_state",
]
