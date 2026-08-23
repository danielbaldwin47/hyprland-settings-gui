"""Dialogs: the config-error list now; Capture, the Migration wizard and confirm-or-revert
later.

`errors.py` is the one error dialog ADR-0016 allows: the `file:line` list the Banner raises,
with a per-Ownership-class action beside each broken file. The auto-revert toast's **Details**
opens the same dialog with no actions on it. Capture and the Migration wizard are #63 and #65.
"""
