"""Restore last good: Snapshot bytes back onto disk, and the model back into step.

ADR-0016's second recovery, and the one that could not be built until the Journal recorded
what each Module version *set* as well as what it contained. The shape of the problem is
worth stating, because it is what every part of this module is answering.

An Apply transaction runs model -> bytes. Modules are rendered whole and deterministically,
so the model is always the source and the file is always the derivative. Restore runs the
other way: the bytes are the source. Laying them down alone would leave the model still
holding the broken version, and the next edit would re-render straight over the recovery.

So the model is brought into step from the restored bytes themselves, read through Lua as
launch reads them (`overrides.written_values`), and not from the compositor: `user.lua` and
a theming tool load after the app's Modules, so a live value may be theirs, and adopting it
would write it into the app's file on the next edit (F3 of the #148 review). Only a key the
bytes could not answer -- or every key, without a Lua interpreter, less the ones a loading
Bridge module sets -- is read off the compositor after the one reload.

Two things this deliberately does **not** do:

* **It does not render the model.** A normal Apply transaction would overwrite the very
  bytes being restored, in the same transaction, before the reload -- which is why this is
  its own operation rather than a flag on `ApplyTransaction`.
* **It does not ask.** Consent is the caller's business (ADR-0016 gates a hand-edited
  Module behind the Banner and suspends that gate when the user is stranded without
  keybinds). By the time a `RestoreTransaction` exists, the decision has been made.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from ..bridge import owners
from ..importer.lua.sandbox import LuaUnavailable
from ..ipc import CommandClient, IpcError
from ..model import ConfigModel
from ..paths import ENTRYPOINT_NAME
from ..schema import ResolvedOption
from ..state import Draft, Journal, LastKnownGood
from ..writer import BeforeReplace, LuaSyntaxError, ProtectedFile, Writer, module_relpath
from .overrides import written_values
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
    live: Sequence[str] | None = None,
) -> ApplyResult:
    """One reload behind the shared in-flight flag, then bring the model into step.

    The step both out-of-band recoveries end in -- Restore last good, which has just laid
    Snapshot bytes down, and an Entrypoint rewrite, which has just changed which files are
    required at all. Neither can confirm itself the way an Apply transaction does: Read-back
    compares the live config against the model, and here the model is the thing being
    corrected rather than the thing being checked.

    The flag is what keeps this reload from being read as somebody else's and answered with
    a full re-read of the config the app is in the middle of repairing (`Reloader`).

    `live` narrows which of `names` are read off the compositor (all of them by default):
    a key the app's own restored bytes already answered must not be, since `user.lua` or a
    theming tool may set it after them (F3 of the #148 review).
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
            await read_state(model, client, _resolve(model, keys if live is None else live))
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


class EntrypointTransaction:
    """Rewrite the Entrypoint, reload, and re-read -- ADR-0016's Quarantine and Entrypoint Fix.

    Both work by rewriting `hyprland.lua` and then needing the compositor to notice. An
    Apply transaction cannot do that job: it renders the model over the App dir, and the
    Entrypoint is the one app-owned file the model does not describe, so the apply would
    reload with the require list it *would* have generated rather than the one the recovery
    just wrote.

    The write runs *inside* the operation, under the queue's lock, rather than before it is
    queued. The Journal has one pending record, and the draft that guards the overwritten
    bytes stays open until the reload answers; every other `Journal.begin` caller runs
    through the same queue, so none can open its own draft in that window and journal this
    one's as `interrupted` (ADR-0010 §Rollback).
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        client: CommandClient,
        reloader: Reloader,
        write: Callable[[BeforeReplace | None], bool],
        journal: Journal | None = None,
        options: Sequence[str] = (),
    ) -> None:
        """`write` replaces the Entrypoint, calling its argument before the rename, and
        returns whether any byte moved -- `Writer.regenerate_entrypoint` or `set_quarantine`.
        """
        self._model = model
        self._client = client
        self._reloader = reloader
        self._write = write
        self._journal = journal
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
        names = self._options
        # Opened before the first byte moves: the Entrypoint Fix overwrites a hand edit by
        # design, and that edit is kept the way Restore last good keeps one.
        draft = self._journal.begin([ENTRYPOINT_NAME]) if self._journal is not None else None
        try:
            changed = self._write(draft.preserve if draft is not None else None)
        except (LuaSyntaxError, ProtectedFile, ValueError, OSError) as error:
            landed = draft.dirty() if draft is not None else ()
            if not landed:
                _log.error("Entrypoint rewrite refused before writing: %s", error)
                if draft is not None:
                    draft.discard()
                outcome = (
                    ApplyOutcome.WRITE_FAILED
                    if isinstance(error, OSError)
                    else ApplyOutcome.ABORTED
                )
                return ApplyResult(outcome, keys=names, detail=str(error))
            _log.error("Entrypoint rewrite failed after the file moved: %s", error)
            result = ApplyResult(ApplyOutcome.WRITE_FAILED, keys=names, detail=str(error))
            self._record(draft, result)
            return result

        if not changed and draft is not None:
            # Nothing was overwritten, so there is nothing to keep. The reload still runs:
            # the user asked for the recovery to take effect, and the compositor may not
            # have loaded what is on disk.
            draft.discard()
            draft = None

        result = await reload_and_reread(
            reloader=self._reloader,
            client=self._client,
            model=self._model,
            names=names,
        )
        self._record(draft, result)
        return result

    @staticmethod
    def _record(draft: Draft | None, result: ApplyResult) -> None:
        """Journal the rewrite; `confirmed` only on a clean reload, as for any other write.

        A still-broken Entrypoint must never become what a later Restore last good puts back.
        The Entrypoint sets no Options, so none are recorded.
        """
        if draft is None:
            return
        draft.commit(
            keys=result.keys,
            outcome=str(result.outcome),
            confirmed=result.outcome is ApplyOutcome.OK,
            changed=[ENTRYPOINT_NAME],
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
                if self._writer.restore(
                    self._model,
                    good.module,
                    good.data,
                    good.options,
                    before_replace=draft.preserve if draft is not None else None,
                )
            ]
        except (LuaSyntaxError, ProtectedFile, ValueError) as error:
            # A Snapshot that will not parse, or one aimed at a file the app must not write.
            # The Writer gates per Module, so an earlier Module may already have been
            # replaced: then the App dir is half-restored, which is `WRITE_FAILED`'s
            # sentence, and the bytes it overwrote are journalled like any other write's.
            landed = draft.dirty() if draft is not None else ()
            if not landed:
                _log.error("restore refused before writing: %s", error)
                if draft is not None:
                    draft.discard()
                return ApplyResult(ApplyOutcome.ABORTED, keys=names, detail=str(error))
            _log.error("restore refused part-way, after %s: %s", ", ".join(landed), error)
            result = ApplyResult(ApplyOutcome.WRITE_FAILED, keys=names, detail=str(error))
            self._record(draft, result, landed)
            return result
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

        live = await self._settle_from_bytes(names)
        result = await reload_and_reread(
            reloader=self._reloader,
            client=self._client,
            model=self._model,
            names=names,
            live=live,
        )
        self._record(draft, result, changed)
        return result

    async def _settle_from_bytes(self, names: Sequence[str]) -> tuple[str, ...]:
        """Put the model in step with the restored Modules from their own bytes, as launch
        does; return the keys that still have to be read off the compositor.

        Not off the compositor first: `user.lua` and a theming tool load after the app's
        Modules, so the live value of a key may be theirs, and the next write would render
        it into the app's file (F3 of the #148 review). A key the restored Module used to
        carry and the Snapshot does not is unset. Without Lua the keys are read live, minus
        the ones a loading Bridge module sets, whose live answer is always the tool's.
        """
        paths = self._writer.paths
        manifest = self._writer.manifest(self._model)
        restored = {good.module for good in self._restores}
        for option, _value in self._model.set_options():
            if module_relpath(option) in restored and option.name not in names:
                self._model.unset(option.name)
        try:
            values = await asyncio.to_thread(
                written_values, paths.app_dir, self._model.schema, manifest, only=restored
            )
        except LuaUnavailable as error:
            _log.warning("no Lua, so the restored Modules are read off Hyprland: %s", error)
            tools = owners(manifest.bridges, quarantined=manifest.quarantined)
            return tuple(name for name in names if name not in tools)
        for name, value in values.items():
            if name not in names:
                continue
            if value is None:
                self._model.set_null(name)
            else:
                self._model.set(name, value)
        return tuple(name for name in names if name not in values)

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
