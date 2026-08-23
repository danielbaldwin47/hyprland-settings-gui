"""Restore last good: Snapshot bytes back onto disk, and the model back into step.

ADR-0016's second recovery, and the one that could not be built until the Journal recorded
what each Module version *set* as well as what it contained. The shape of the problem is
worth stating, because it is what every part of this module is answering.

An Apply transaction runs model -> bytes. Modules are rendered whole and deterministically,
so the model is always the source and the file is always the derivative. Restore runs the
other way: the bytes are the source, and they are bytes *this* model cannot produce -- they
are what an earlier model rendered, and the app cannot read its own Lua back to reconstruct
that one (#62). Laying them down alone would leave the model still holding the broken
version, and the next edit would re-render straight over the recovery.

So the model is brought into step from the one place that does know what the restored bytes
mean: **the compositor that just loaded them**. Write the Snapshot, reload once, then re-read
exactly the Options the Journal recorded those bytes as setting. That is the same mechanism
the app already recovers its model with at startup (`reread.py`), pointed at one Module
instead of the whole App dir -- not a new trick, and not one that waits on #62.

Two things this deliberately does **not** do:

* **It does not render the model.** A normal Apply transaction would overwrite the very
  bytes being restored, in the same transaction, before the reload -- which is why this is
  its own operation rather than a flag on `ApplyTransaction`.
* **It does not ask.** Consent is the caller's business (ADR-0016 gates a hand-edited
  Module behind the Banner and suspends that gate when the user is stranded without
  keybinds). By the time a `RestoreTransaction` exists, the decision has been made.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..ipc import CommandClient, IpcError
from ..model import ConfigModel
from ..schema import ResolvedOption
from ..state import Draft, Journal, LastKnownGood
from ..writer import LuaSyntaxError, ProtectedFile, Writer
from .reread import read_state
from .result import ApplyOutcome, ApplyResult
from .transaction import Reloader

_log = logging.getLogger(__name__)


async def reload_and_reread(
    *,
    reloader: Reloader,
    client: CommandClient,
    model: ConfigModel,
    names: Sequence[str],
) -> ApplyResult:
    """One reload behind the shared in-flight flag, then bring the model into step.

    The step both out-of-band recoveries end in -- Restore last good, which has just laid
    Snapshot bytes down, and an Entrypoint rewrite, which has just changed which files are
    required at all. Neither can confirm itself the way an Apply transaction does: Read-back
    compares the live config against the model, and here the model is the thing being
    corrected rather than the thing being checked.

    The flag is what keeps this reload from being read as somebody else's and answered with
    a full re-read of the config the app is in the middle of repairing (`Reloader`).
    """
    keys = tuple(names)
    with reloader.confirming():
        report = await reloader.reload()
        if report.failed is not None:
            return ApplyResult(report.failed, keys=keys, detail=report.detail)

        try:
            # Even when the reload reported errors. The file just written may well have
            # loaded while a *different* one is what is broken, and re-reading is how the
            # model finds out which -- refusing to look would leave it describing the
            # version that was just replaced.
            await read_state(model, client, _resolve(model, keys))
        except IpcError as error:
            return ApplyResult(ApplyOutcome.COMPOSITOR_GONE, keys=keys, detail=str(error))

    if report.errors:
        return ApplyResult(
            ApplyOutcome.CONFIG_ERRORS, keys=keys, errors=report.errors, binds=report.binds
        )
    return ApplyResult(ApplyOutcome.OK, keys=keys)


def _resolve(model: ConfigModel, names: Sequence[str]) -> tuple[ResolvedOption, ...]:
    """The Options to re-read, skipping any this Schema no longer knows.

    A Snapshot can outlive an Option: it was recorded under one Hyprland version and may be
    restored under another (ADR-0012). Asking about a name the Schema dropped would raise
    where the recovery is meant to be doing its most careful work.
    """
    resolved = []
    for name in names:
        option = model.schema.get(name)
        if option is None:
            _log.info("restored Module sets %s, which this Schema no longer has", name)
            continue
        resolved.append(option)
    return tuple(resolved)


class ReloadTransaction:
    """Reload and re-read, writing nothing -- what an Entrypoint change needs.

    ADR-0016's Quarantine and its Entrypoint Fix both work by rewriting `hyprland.lua` and
    then needing the compositor to notice. An Apply transaction cannot do that job: it
    renders the model over the App dir, and the Entrypoint is the one app-owned file the
    model does not describe, so the apply would reload with the require list it *would* have
    generated rather than the one the recovery just wrote.

    Still a queued operation rather than a bare `reload()` call, because it ends in reading
    `configerrors` -- which an apply or a preview running alongside would overwrite.
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        client: CommandClient,
        reloader: Reloader,
        options: Sequence[str] = (),
    ) -> None:
        self._model = model
        self._client = client
        self._reloader = reloader
        self._options = tuple(options)

    @property
    def options(self) -> tuple[str, ...]:
        """What to re-read afterwards -- everything the app owns.

        Wider than a restore's, and it has to be: quarantining `user.lua` changes the value
        of every Option that file was overriding, and the app cannot know which those were
        without asking about all of them.
        """
        return self._options

    async def run(self, keys: Sequence[str]) -> ApplyResult:
        """`keys` is ignored -- the Options to re-read were fixed at construction."""
        return await reload_and_reread(
            reloader=self._reloader,
            client=self._client,
            model=self._model,
            names=self._options,
        )


class RestoreTransaction:
    """One Restore last good, over one or more Modules, as a queue-able operation.

    Built per restore rather than reused, because what it restores *is* its state: a
    `LastKnownGood` is a decision already taken about specific bytes, and an object that
    could be re-run against different Modules would invite exactly the "restore whatever is
    newest" behaviour ADR-0016 rules out.
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        writer: Writer,
        client: CommandClient,
        reloader: Reloader,
        restores: Sequence[LastKnownGood],
        journal: Journal | None = None,
    ) -> None:
        self._model = model
        self._writer = writer
        self._client = client
        self._reloader = reloader
        self._restores = tuple(restores)
        self._journal = journal

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(good.module for good in self._restores)

    @property
    def options(self) -> tuple[str, ...]:
        """Every Option the restored bytes set, deduplicated, in Module order.

        What the re-read asks about, and what the resulting `ApplyResult` reports as its
        keys. A restore of a Module that set nothing -- the Entrypoint -- contributes none,
        and a restore made entirely of those still reloads: the require list changing is a
        change to the config whether or not any Option moved with it.
        """
        seen: dict[str, None] = {}
        for good in self._restores:
            for name in good.options:
                seen[name] = None
        return tuple(seen)

    async def run(self, keys: Sequence[str]) -> ApplyResult:
        """Write the Snapshots, reload once, and re-read what they set.

        `keys` is ignored: a restore is accountable for the Options its own Snapshots
        recorded, and those are already in hand. It is in the signature so this satisfies the
        queue's `Transaction` protocol -- the same lock has to cover a restore and an apply,
        because both end in a reload and `configerrors` is one global slot.
        """
        names = self.options
        if not self._restores:
            return ApplyResult(ApplyOutcome.NOTHING_TO_DO, keys=names)

        # Opened before the first byte moves. The bytes being overwritten may be a hand edit
        # -- under §Zero-binds this path runs without asking -- and ADR-0016 promises that
        # edit is "preserved in the Journal". This is that promise.
        draft = self._journal.begin(self.modules) if self._journal is not None else None

        try:
            changed = [
                good.module
                for good in self._restores
                if self._writer.restore(self._model, good.module, good.data, good.options)
            ]
        except (LuaSyntaxError, ProtectedFile, ValueError) as error:
            # A Snapshot that will not parse, or one aimed at a file the app must not write.
            # Nothing partial is left behind that the caller can act on, and saying so beats
            # reporting a recovery that did not happen.
            _log.error("restore refused before writing: %s", error)
            if draft is not None:
                draft.discard()
            return ApplyResult(ApplyOutcome.ABORTED, keys=names, detail=str(error))
        except OSError as error:
            _log.error("restore failed mid-write: %s", error)
            result = ApplyResult(ApplyOutcome.WRITE_FAILED, keys=names, detail=str(error))
            self._record(draft, result, draft.dirty() if draft is not None else ())
            return result

        if not changed:
            # The Snapshot is already what is on disk. Reloading would spend a full teardown
            # to reassert bytes the compositor has, and the model is already in step with
            # them -- there is nothing here to recover from.
            if draft is not None:
                draft.discard()
            return ApplyResult(ApplyOutcome.NOTHING_TO_DO, keys=names)

        result = await reload_and_reread(
            reloader=self._reloader,
            client=self._client,
            model=self._model,
            names=names,
        )
        self._record(draft, result, changed)
        return result

    def _record(self, draft: Draft | None, result: ApplyResult, changed: Sequence[str]) -> None:
        """Journal the restore like any other write that reached disk.

        A restore is history too: it replaced bytes, and the next recovery needs to be able
        to see what they were. `confirmed` follows the same rule as everywhere else -- only a
        clean reload establishes a new Last known good, so a restore into a config that is
        still broken never becomes the thing a later restore restores to.
        """
        if draft is None:
            return
        draft.commit(
            keys=result.keys,
            outcome=str(result.outcome),
            confirmed=result.outcome is ApplyOutcome.OK,
            changed=list(changed),
            options={good.module: good.options for good in self._restores},
        )
