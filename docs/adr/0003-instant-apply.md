# ADR-0003: Instant apply

**Status:** accepted — 2026-08-19

Read by the agent about to change instant apply, before the first edit; the Status line says what is on `main` now.

## Decision

Changes write to config immediately (Hyprland auto-reloads on save); per-option reset-to-default and undo instead of an Apply/Revert stage. GNOME-Settings feel.

## Consequences

Reload semantics (what a reload clears/re-runs, cost of frequent reloads, error surfacing) must be researched before the apply pipeline is designed.
