"""The full state re-read: live compositor values back into the model.

Read-back (`transaction.py`) asks "did the keys I just wrote land?" and answers per key
without touching the model. This asks the opposite question -- "what does the live config
say, and what should the model therefore hold?" -- and writes the answer in. ADR-0010 calls
for it in two places, and they differ only in which Options are read:

* **Startup.** The app has to recover the values it wrote in an earlier session. It reads
  the Options belonging to Modules the Manifest says it owns (`app_owned_options`), so a
  fresh install adopts nothing and starts Unset, as the spec's from-scratch user expects.
* **Foreign reload.** "Any `configreloaded` not correlated with an in-flight transaction
  triggers a full state re-read + drift scan" -- somebody else rewrote the config, so every
  Option the model holds is re-read and the stale ones are dropped.

Three rules, each learned from a defect the schema layer already names:

1. **A sentinel is not a value.** `getoption` answers `[[EMPTY]]` for an unset string
   (verified live), and adopting that verbatim would put the marker in the model, in the
   Row, and eventually in the user's Lua. A reply that spells the Option's "no value" lands
   as explicit null.
2. **An explicit null is never re-read over.** The model already knows the key means "no
   value"; what the compositor reports for that marker is its own interpretation, and
   parsing it back would turn "same as the outer gaps" into four gaps of -1.
3. **Unreadable is not empty.** A reply this Option's parser refuses leaves the model
   alone. It is a Hyprland-version surprise, not evidence that the value is gone -- the
   same distinction `Unconfirmed` draws for Read-back.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..ipc import CommandClient, NoSuchOption
from ..model import ConfigModel, getoption_raw, parse_getoption
from ..schema import ResolvedOption, Schema
from ..schema.infer import STRING_SENTINELS
from ..state import Manifest
from ..writer import is_option_module, module_stem

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReRead:
    """What one re-read changed, per Option, in the four ways it can end.

    Counts alone would not do: `unreadable` and `unknown` are the pair a caller has to be
    able to tell apart from "nothing was set", because both mean the model kept a value the
    compositor did not confirm.
    """

    adopted: tuple[str, ...] = ()
    """Keys the live config sets; the model now holds the live value (or explicit null)."""

    cleared: tuple[str, ...] = ()
    """Keys the model held but the live config no longer sets; now Unset."""

    unreadable: tuple[str, ...] = ()
    """Keys whose reply this Option's parser refused. The model was left alone."""

    unknown: tuple[str, ...] = ()
    """Keys this compositor has never heard of -- a schema newer than the running Hyprland
    (ADR-0012). The model was left alone; the Row's own degradation badge is #77's."""

    @property
    def changed(self) -> bool:
        return bool(self.adopted or self.cleared)


def app_owned_options(schema: Schema, manifest: Manifest) -> tuple[ResolvedOption, ...]:
    """Every Option of every Section the app has a Module for.

    Ownership is per **Module**, not per Option, because a Module is the finest grain the
    Manifest records -- and the app cannot yet read its own Lua back to learn which keys are
    inside one. Per-Option ownership arrives with the Lua reader (#62); until then this is
    deliberately the conservative half of the trade: a Section the app never wrote is never
    adopted, so nothing that lives only in `user.lua` is quietly taken over on first run.
    """
    owned = {
        relpath.rsplit("/", 1)[-1].removesuffix(".lua")
        for relpath in manifest.modules
        if is_option_module(relpath)
    }
    return tuple(option for option in schema.options if module_stem(option) in owned)


async def read_state(
    model: ConfigModel,
    client: CommandClient,
    options: Sequence[ResolvedOption],
) -> ReRead:
    """Read `options` off the live compositor and make the model agree with them.

    Sequential rather than gathered: a round-trip is 0.4 ms (ADR-0010), so even the widest
    re-read this app performs is a few tens of milliseconds, and 353 concurrent connections
    to a socket that serves one request per connection buys nothing but a thundering herd.
    """
    adopted: list[str] = []
    cleared: list[str] = []
    unreadable: list[str] = []
    unknown: list[str] = []

    for option in options:
        try:
            reply = await client.getoption(option.name)
        except NoSuchOption:
            unknown.append(option.name)
            continue

        if not reply.set_by_user:
            if model.is_set(option.name):
                model.unset(option.name)
                cleared.append(option.name)
            continue

        if model.is_set(option.name) and model.get(option.name) is None:
            # Rule 2: the model's explicit null already says what this key means.
            continue

        payload = dict(reply.payload)
        try:
            if _is_no_value(option, payload):
                model.set_null(option.name)
            else:
                model.set(option.name, parse_getoption(option, payload))
        except (KeyError, ValueError, TypeError) as error:
            _log.warning("unreadable getoption reply for %s: %s", option.name, error)
            unreadable.append(option.name)
            continue

        adopted.append(option.name)

    return ReRead(
        adopted=tuple(adopted),
        cleared=tuple(cleared),
        unreadable=tuple(unreadable),
        unknown=tuple(unknown),
    )


def _is_no_value(option: ResolvedOption, payload: dict[str, object]) -> bool:
    """Whether a reply spells this Option's "no value" rather than a value.

    Compared raw, against both spellings a sentinel has: the Overlay's curated `null_value`
    (`""`, `-1`) and whatever `descriptions` originally printed (`[[EMPTY]]`, `[[Auto]]`),
    which is what the compositor still answers with. Only nullable Options can reach this --
    for anything else the marker *is* the value.
    """
    if not option.nullable:
        return False

    raw = getoption_raw(option, payload)
    if isinstance(raw, str) and raw in STRING_SENTINELS:
        return True
    return option.null_value is not None and raw == option.null_value
