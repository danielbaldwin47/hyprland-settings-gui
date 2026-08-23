"""The engine/UI seam, enforced as a build invariant (ADR-0011).

`hyprtweaker.engine` is the UI-free half of the app: schema, model, writer,
importer, ipc, state. It must stay importable with no GTK anywhere in reach,
because it is the half that runs in tests, in the schema generator, and in any
future CLI. These tests import every engine module with `gi` masked out, so the
build fails the moment the seam leaks.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.abc import MetaPathFinder

import pytest

ENGINE = "hyprtweaker.engine"


def _is_masked(name: str) -> bool:
    """True for the module names this mask owns: `gi` and `hyprtweaker`, with subtrees."""
    return name in ("gi", "hyprtweaker") or name.startswith(("gi.", "hyprtweaker."))


class _GiMask(MetaPathFinder):
    """A meta-path finder that refuses `gi` and everything under it.

    Raising from `find_spec` rather than returning None is deliberate: it is what
    lets the failure carry a message naming the actual rule, which is the whole
    value of this test to whoever trips it.
    """

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> None:
        if fullname == "gi" or fullname.startswith("gi."):
            raise ImportError(
                f"{fullname} is masked: hyprtweaker.engine must not import gi (ADR-0011)"
            )
        return None


@contextmanager
def gi_masked() -> Iterator[None]:
    """Run the block with `gi` unimportable and hyprtweaker unloaded.

    Both halves matter: masking `gi` alone would prove nothing if the engine
    modules were already imported (and had already pulled `gi` in) before the
    mask went up, so every `hyprtweaker.*` and `gi*` module is evicted first and
    restored afterwards.

    Entry and exit are deliberately symmetric, touching only the names this mask
    owns. A blunter restore (clear `sys.modules`, put the snapshot back) also
    evicts anything *else* first imported inside the block -- a later import then
    builds a second, distinct module object, silently discarding whatever state
    the first one held. That corrupts the session for every test that follows.
    """
    saved = {name: module for name, module in sys.modules.items() if _is_masked(name)}
    for name in list(sys.modules):
        if _is_masked(name):
            del sys.modules[name]

    mask = _GiMask()
    sys.meta_path.insert(0, mask)
    try:
        yield
    finally:
        sys.meta_path.remove(mask)
        for name in list(sys.modules):
            if _is_masked(name):
                del sys.modules[name]
        sys.modules.update(saved)


def _discovery_failed(name: str) -> None:
    # walk_packages defaults to onerror=None, which swallows import errors and
    # silently drops the subtree -- a broken engine package would then read as a
    # passing seam. Turn it into a loud failure instead.
    raise RuntimeError(f"failed to walk {name} while discovering engine modules")


def engine_module_names() -> list[str]:
    """Every module and subpackage under `hyprtweaker.engine`, including itself."""
    engine = importlib.import_module(ENGINE)
    names = [ENGINE]
    names += [
        info.name
        for info in pkgutil.walk_packages(
            engine.__path__, prefix=f"{ENGINE}.", onerror=_discovery_failed
        )
    ]
    return sorted(names)


def test_engine_has_modules_to_check() -> None:
    """Guards the suite itself: a discovery bug must not read as a passing seam."""
    names = engine_module_names()
    assert ENGINE in names
    # The six engine subpackages of ADR-0011.
    expected = {
        f"{ENGINE}.{sub}" for sub in ("schema", "model", "writer", "importer", "ipc", "state")
    }
    assert expected <= set(names), f"missing engine subpackages: {expected - set(names)}"


def test_the_mask_actually_masks() -> None:
    """Guards the mask: if `gi` stayed importable, the seam test would be a no-op."""
    with gi_masked(), pytest.raises(ImportError):
        importlib.import_module("gi")


@pytest.mark.parametrize("module_name", engine_module_names())
def test_engine_module_imports_without_gi(module_name: str) -> None:
    with gi_masked():
        importlib.import_module(module_name)
