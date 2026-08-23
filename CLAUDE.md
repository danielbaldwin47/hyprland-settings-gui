# hyprland-settings-gui

A polished GUI settings app for Hyprland that reads and writes the new Lua config (`hl.*` API, Hyprland ≥ 0.56).

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues on this repo (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Hyprland release check

On a new Hyprland release (`Release check: Hyprland <ver>` issue), follow `docs/agents/hyprland-release-check.md`.

### Self-landing

Agents merge their own PRs — pre-authorized, background agents included. Gates and merge steps: `docs/agents/self-landing.md`.

### Needs from you

Leftovers for the human — tasks or decisions surfacing during ticket work — go to the inbox issue and never block the chain. Protocol: `docs/agents/needs-from-you.md`.

### Code review

`/code-review` in this repo always means `mattpocock-skills:code-review` — the two-axis (Standards / Spec) review.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
