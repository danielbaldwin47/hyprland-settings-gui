"""The UI shell: everything that touches GTK4 / libadwaita.

Kept deliberately thin -- all logic worth testing lives in the Engine
(ADR-0011). This package imports ``gi``; the Engine never does.
"""
