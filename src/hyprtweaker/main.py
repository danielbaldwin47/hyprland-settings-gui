"""Process entrypoint.

Kept separate from :mod:`hyprtweaker.application` and free of module-level
``gi`` imports, so that the installed launcher and ``python -m hyprtweaker``
share one entrypoint without the top-level package pulling GTK in.
"""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str]) -> int:
    """Run the app. Returns the process exit status."""
    from hyprtweaker.application import HyprtweakerApplication

    return HyprtweakerApplication().run(list(argv))
