# ADR-0013: Row conventions for generated option rows

**Status:** accepted — 2026-08-22

## Context

Prototype #8 generated all 353 option Rows from the Schema and surfaced a set of presentation questions the spec needs settled (ticket #19):

- `AdwEntryRow` has no subtitle, so the 24 plain string options lost their description line.
- Rows disabled by `depends_on` need a presentation (the prototype hid help in prose).
- The three `AdwExpanderRow` widgets (gradient ×16, vec2 ×6, css-gaps ×3) show nothing useful while collapsed.
- The `advanced`/`hidden` visibility tiers need a disclosure affordance (the prototype used a temporary header ToggleButton).
- Per-row reset and the wiki `help_url` needed a home on the Row.
- `CONTEXT.md` said the Row subtitle is the dotted key; prototype #8 used the description and the curated help text. The conflict had to be resolved.

Every choice below was re-prototyped on the #8 codebase and screenshotted in a nested Hyprland; images live in `docs/design/row-catalogue/` and the full catalogue in `docs/design/row-catalogue.md`.

## Decision

A generated Row is: **title, description as subtitle, typed control, and a fixed-order suffix strip**:

```
[state pills: Advanced · Restart] [value summary] [dependency badge] [reset (when modified)] [ⓘ help]
```

1. **Subtitle = description.** The subtitle is the option's description (the curated `help` text when the Overlay overrides it). The dotted key moves into the ⓘ popover (copyable) and stays in the search index — typing `kb_layout` still finds the Row. This supersedes the dotted-key-subtitle wording in `CONTEXT.md`.

2. **String rows** (24 options): `AdwActionRow` + `GtkEntry` suffix instead of `AdwEntryRow` — keeps the subtitle, at the cost of a narrower input. For nullable strings the entry's `placeholder-text` is the `null_label` ("Device default", "None", …) so an unset string never renders as a blank field. Commit on Enter / focus-out. Roughly 15 of the 24 get real pickers via Overlay curation (xkb pickers, font, file chooser, monitor dropdown); the plain-entry case is the fallback, not the norm.

3. **`depends_on`-disabled Rows stay visible**: only the *control* is made insensitive (title/subtitle remain readable), plus a suffix badge "Requires \<controlling option title\>" that navigates to the controlling Row on click. Never hide the Row — hiding makes options undiscoverable and makes group layouts jump. Control-only insensitivity is deliberate: libadwaita's row-level `sensitive` dims the whole Row including its text.

4. **ExpanderRow collapsed summary**: a dim-label value preview as a suffix — gradient: colour-swatch strip + angle ("45°"); css-gaps: "8" when uniform, "8 · 12 · 8 · 12" (top·right·bottom·left) otherwise; vec2: "0.0, 0.5". The Row answers "what is it set to?" without expanding.

5. **Advanced/hidden disclosure**: one global **"Show advanced settings"** switch in the primary (hamburger) menu — not per-page. Advanced Rows render in place inside their normal Groups. The `hidden` tier (`debug`, `quirks`, `experimental`, `input-capture`) appears only in the Config view with the switch on, never in Tasks. Search always indexes everything; navigating to a hit reveals that Row one-off even with the switch off.

6. **Per-row reset**: a suffix `edit-undo-symbolic` icon button, visible only when the Option is modified (per-type is-default check: float epsilon, sentinel normalisation). Tooltip "Reset to default: \<value\>". Reset means **Unset** (stop emitting; ADR-0005 tri-state), not write-the-default-value.

   *Amended during #57 — "modified" is the tri-state, not a comparison.* The per-type is-default check above is prototype #8 heritage: #8 had no model, so the only way to ask "has this been touched?" was to compare the value against the default, and that comparison needed float epsilon and sentinel normalisation to be honest. The real model answers the question directly — an Option is modified exactly when the model emits it (`ConfigModel.is_set`). The two disagree in one case, and ADR-0005 settles it in favour of the tri-state: an Option deliberately set to today's default *is* modified, keeps its value when upstream changes the default, and must offer the reset that takes that decision back. A comparison would hide the arrow on exactly that Row. The rest of this clause stands unchanged.

7. **Wiki link**: inside the per-row ⓘ popover, which holds — help text, dotted key (selectable), default value, and "Learn more on the wiki" (`help_url`, section anchor). No extra link chrome on the Row itself.

## Consequences

- The Row vocabulary is closed: every widget type composes the same suffix strip, so the Row factory (ADR-0011 `ui/rows/`) has one convention to implement, and `/to-spec` can reference "value summary", "dependency badge", "help popover" as named parts.
- `CONTEXT.md`'s Row term is updated (description-subtitle); Help popover, Value summary, Dependency badge, Advanced switch enter the glossary.
- The Overlay gains work, not the schema: `null_label` becomes load-bearing for every nullable string (a missing one leaks sentinels like `[[EMPTY]]` into placeholders — the CI completeness test from ADR-0011 must cover it), and `depends_on` targets must carry curated titles for the badge text.
- Control-only insensitivity requires reaching the inner control widget per row type — the Row factory must expose its control, not have the badge code walk the widget tree (the prototype walked; the real factory returns the handle).
- Search must index dotted keys and hidden-tier options, and result navigation needs the one-off reveal path (already on the map as the search fog item).

## Alternatives considered

- **Dotted key as subtitle** (CONTEXT.md's original wording) — rejected: users scan for what an option *does*; the key is expert metadata, still reachable in the popover and by search.
- **`AdwEntryRow` for strings** — rejected: no subtitle slot in libadwaita, and 24 description-less Rows is exactly the falsehood-by-omission #8 warned about.
- **Hiding `depends_on`-disabled Rows** — rejected: undiscoverable, layout jumps, and the user can't learn *why* something is off.
- **Per-page Advanced toggle** — rejected: 21 Config pages each with their own switch is state the user has to re-toggle everywhere; disclosure is a persona-level choice (ADR-0004), not a per-page one.
- **Reset writes the default value** — rejected: contradicts ADR-0005; an explicitly-set default survives Hyprland default changes, which is not what "reset" promises.
- **Wiki link as a visible Row suffix** — rejected: 353 identical link icons is noise; the popover already gathers all reference material in one place.
