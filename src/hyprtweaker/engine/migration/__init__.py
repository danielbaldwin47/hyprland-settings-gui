"""Migration, first run and Import/Export -- the guarded flow around both importers.

ADR-0009 in code. The importers turn somebody else's config into a model; everything here
is about doing that *safely* on a machine the user is sitting in front of: what kind of
config is there (`detect`), a copy of it before anything moves (`backup`), a marker that
survives the app dying mid-switch (`sentinel`), the five-step state machine itself (`flow`),
and the one flattened file that carries a config back out again (`export`).

Deliberately toolkit-free, like the rest of the engine: the wizard dialog renders a
`MigrationFlow`, and a test drives one to completion -- including the rollback paths --
without a display.
"""

from .backup import Backup, create, latest, restore, stored
from .detect import ConfigKind, Detection, detect
from .export import ExportResult, render
from .flow import (
    ROLLBACK_SECONDS,
    Check,
    Client,
    Decision,
    MigrationFlow,
    Preview,
    Step,
    SwitchResult,
    VerifyGate,
    fresh_start,
)
from .sentinel import Sentinel

__all__ = [
    "ROLLBACK_SECONDS",
    "Backup",
    "Check",
    "Client",
    "ConfigKind",
    "Decision",
    "Detection",
    "ExportResult",
    "MigrationFlow",
    "Preview",
    "Sentinel",
    "Step",
    "SwitchResult",
    "VerifyGate",
    "create",
    "detect",
    "fresh_start",
    "latest",
    "render",
    "restore",
    "stored",
]
