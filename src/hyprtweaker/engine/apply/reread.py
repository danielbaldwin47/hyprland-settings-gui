"""The full state re-read: live compositor values back into the model.

Read-back (`transaction.py`) asks "did the keys I just wrote land?" and answers per key
without touching the model. This asks the opposite question -- "what does the live config
say, and what should the model therefore hold?" -- and writes the answer in. ADR-0010 calls
for it in two places, and they differ only in which Options are read:

* **Startup.** The app has to recover the values it wrote in an earlier session. It reads
  exactly the Options the Manifest records it having written (`app_owned_options`), so a
  fresh install adopts nothing and starts Unset, as the spec's from-scratch user expects.
* **Foreign reload.** "Any `configreloaded` not correlated with an in-flight transaction
  triggers a full state re-read + drift scan" -- somebody else rewrote the config, so the
  re-read has to cover both what the model currently holds *and* everything the app owns.
  Narrowing it to the former would miss a key a foreign edit added inside an app-owned
  Module: the Row would keep showing the default, and the next Apply would re-render that
  Module without the key, silently overwriting the hand edit ADR-0016 exists to protect.

Three rules, each learned from a defect the schema layer already names:

1. **A sentinel is not a value.** `getoption` answers `[[EMPTY]]` for an unset string
   (verified live), and adopting that verbatim would put the marker in the model, in the
   Row, and eventually in the user's Lua. A reply that spells the Option's "no value" lands
   as explicit null.
2. **An explicit null is never re-read over.** A re-read exists to replace a claim with
   better information, and here there is none to be had: `getoption` has no spelling for
   "this key has no value", so a compositor asked about one answers with whatever the
   marker resolved to. Parsing that back would turn "same as the outer gaps" into four gaps
   of -1. `ApplyTransaction._compare` already stops at "the live config sets this key" for
   exactly this reason, and the two must not disagree.

   That holds even when something else has since overridden the key: the model records what
   *this app* sets, and an override is surfaced by the ADR-0005 drift badge (#57), never by
   rewriting the model out from under the user's own choice.
3. **Unreadable is not empty.** A reply this Option's parser refuses leaves the model
   alone. It is a Hyprland-version surprise, not evidence that the value is gone -- and it
   is judged by the same `live_value` Read-back uses, so the two cannot drift into
   disagreeing about what "unreadable" means.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..ipc import CommandClient, NoSuchOption
from ..model import ConfigModel, getoption_raw
from ..schema import ResolvedOption, Schema
from ..schema.infer import STRING_SENTINELS
from ..state import Manifest
from ..writer import is_option_module
from .result import UNREADABLE, live_value

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
    """Exactly the Options the app's own Modules set, as the Manifest last recorded them.

    Per Option, not per Section. The app cannot read its own Lua back (#62), so the
    Manifest is the only record of what it wrote -- and "every Option in a Section the app
    has a Module for" would be an over-claim: an Option `user.lua`, `legacy.lua` or a Bridge
    sets in that same Section would be adopted, shown as the app's own, and emitted into the
    app's Module on the next write. That value would then survive the user deleting their
    own line, which is the app quietly taking over a file it promised never to touch
    (ADR-0005).

    Ordered by Hyprland's declaration order rather than by the Manifest, so a re-read walks
    the sockets in the same order every time.
    """
    owned = {
        name
        for relpath, record in manifest.modules.items()
        if is_option_module(relpath)
        for name in record.options
    }
    return tuple(option for option in schema.options if option.name in owned)


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

        payload = dict(reply.payload)
        try:
            no_value = _is_no_value(option, payload)
        except KeyError as error:
            _log.warning("unreadable getoption reply for %s: %s", option.name, error)
            unreadable.append(option.name)
            continue

        if model.is_set(option.name) and model.get(option.name) is None:
            # Rule 2: the model's explicit null already says what this key means, and no
            # reply can say it better.
            continue

        if no_value:
            model.set_null(option.name)
            adopted.append(option.name)
            continue

        value = live_value(option, payload)
        if value is UNREADABLE:
            unreadable.append(option.name)
            continue

        try:
            model.set(option.name, value)
        except (ValueError, TypeError) as error:
            # `live_value` produced something of the Option's own type; the model refusing
            # it means a bound or an enum the compositor does not share. Same answer as an
            # unreadable reply -- no evidence the model's value is wrong.
            _log.warning("live value rejected by the model for %s: %s", option.name, error)
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
