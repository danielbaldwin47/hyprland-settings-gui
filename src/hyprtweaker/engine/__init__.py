"""The Engine: the UI-free half of hyprtweaker (ADR-0011).

Schema loading, the config model, the writer, the importers, IPC and
on-disk state all live here. Nothing under this package may import ``gi``
-- the seam is enforced by ``tests/unit/test_engine_seam.py``, so a stray
GTK import fails the build rather than the app.
"""
