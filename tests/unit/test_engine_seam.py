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


class _MaskedImportError(ImportError):
    """Raised in place of a successful `gi` import while the mask is active."""


class _GiMask(MetaPathFinder):
    """A meta-path finder that refuses `gi` and everything under it."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> None:
        if fullname == "gi" or fullname.startswith("gi."):
            raise _MaskedImportError(
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
    """
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if name == "gi" or name.startswith(("gi.", "hyprtweaker.")) or name == "hyprtweaker":
            del sys.modules[name]

    mask = _GiMask()
    sys.meta_path.insert(0, mask)
    try:
        yield
    finally:
        sys.meta_path.remove(mask)
        sys.modules.clear()
        sys.modules.update(saved)


def engine_module_names() -> list[str]:
    """Every module and subpackage under `hyprtweaker.engine`, including itself."""
    engine = importlib.import_module(ENGINE)
    names = [ENGINE]
    names += [info.name for info in pkgutil.walk_packages(engine.__path__, prefix=f"{ENGINE}.")]
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
