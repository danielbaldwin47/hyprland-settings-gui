# ADR-0017: Settings search

**Status:** accepted — 2026-08-22

## Context

Search was fixed early as view-independent and all-indexing: it sees every Option including the hidden tier, and navigating to a hit reveals the Row one-off (ADR-0013); the dotted key lives in the search index and Help popover, not the subtitle. The IA prototype (#7) shipped a `Search.dc.html` mock: search entry in the sidebar header, grouped results replacing the nav list, the content pane live-previewing the selected hit. Open were the surface (palette vs sidebar), index scope, matching, the navigation target when both Views hold the Option, and how the index is built.

## Decision

### Surface: sidebar search, not a palette

The GNOME Settings pattern, as mocked: the magnifier button in the sidebar header reveals a search entry; while the query is non-empty, grouped results replace the nav list, and selecting a result shows its Page in the content pane with the target Row flash-highlighted — the ADR-0013 one-off reveal. Clearing or escaping restores the nav list.

**Amended during #72 — the entry sits *below* the sidebar header, not in its title slot.** This clause originally read "swaps the sidebar title for a search entry", following the `Search.dc.html` mock. The platform will not have it, and the reason is worth recording so nobody re-derives it. GTK's type-to-search handler begins `if (!gtk_widget_get_mapped (bar)) return GDK_EVENT_PROPAGATE` (`gtksearchbar.c`), so a `GtkSearchBar` must stay **mapped even while the finder is closed** — which rules out parking it in a `GtkStack` page, hiding it, or swapping it in and out of the title slot. Each of those leaves every widget pointer intact while the shortcut silently does nothing, so only a mapping assertion or a real keystroke catches it. Sharing the title slot with the title *does* keep it mapped, but a closed `GtkSearchBar` collapses only vertically (its revealer slides down) and still measures min 85 / nat 224 px wide, which truncated the sidebar title to "Hyp…".

Below the header is the arrangement the widget is designed for, and the only one correct on both counts: the title reads and typing opens the finder. **Type-to-search is a requirement of this ADR; the entry's placement within the sidebar is choreography** — so when the two proved mutually exclusive, the requirement won. The sidebar remains the search surface, which is what the palette alternative was rejected over; only the mock's pixel placement is given up.

Shortcuts: **Ctrl+F** focuses search, and **type-to-search** — typing while focus is not in a text entry — focuses it too. Both are GNOME HIG conventions. No Ctrl+K overlay palette: foreign to libadwaita, and the sidebar already is the navigation surface.

### Index scope: Options and Entities

Two result groups, in order:

- **Settings** — every Option, searched by title, description/curated help, and dotted key. All visibility tiers, per ADR-0013.
- **Rules & entities** — Binds by key combo, dispatcher name, and command; window/layer Rules by Label and Match; Workspace rules by selector; Monitor rules by connector and description; Monitor profiles and Presets by name.

**Actions are cut.** The mock's third group ("Reset every gap to its default", "Show gaps in the wiki") is a generated-bulk-command feature wearing a search costume. Wiki links already live in every Row's Help popover (ADR-0013); per-Row reset plus undo covers resetting. Deliberately dropped, not deferred fog.

### Matching and ranking

Case-insensitive **substring** match over all indexed fields, with a word-prefix boost. Rank: title prefix > title substring > dotted-key substring > any other field; ties break by Page order. **No fuzzy matching** — over a 353-option corpus fuzzy is noise, and dotted keys make substring exact for experts.

### Navigation target

A hit resolves against the **active View** first. Only when the Row has no home there — the hidden tier while in Tasks — does navigation switch the segment to Config. That switch is the ordinary View switch, visible in the control and remembered like any manual toggle: one mechanism, no temporary hidden state.

### Index build

One in-memory index, built at startup from the Schema (Generated + Overlay) and the model; Entity entries are rebuilt on the model-change signal. The corpus is under a thousand rows, so rebuilds are sub-millisecond. No persistence, no per-query rebuild.

## Consequences

- The one-off reveal path (ADR-0013) must be reachable from search navigation: scroll-to-Row, flash, and temporary visibility for advanced/hidden hits.
- The Overlay's curated titles are index input — the CI completeness test (ADR-0011) already guarantees they exist.
- Entity search fields come from the model, so the index subscribes to the same change signal Read-back drives (ADR-0010).
- The sidebar's search-results mode needs keyboard traversal (arrows + Enter) since Ctrl+F users won't reach for the mouse.

## Alternatives considered

- **Ctrl+K command palette overlay** — rejected: no libadwaita precedent, duplicates the sidebar as a navigation surface, and the mock validated the sidebar pattern.
- **Actions in results** (bulk reset, wiki links) — rejected for v1: a distinct feature with its own correctness surface (enumeration semantics, undo), low marginal value over per-Row affordances.
- **Fuzzy matching** — rejected: noisy over a small corpus; substring + prefix boost is predictable and exact on dotted keys.
- **Navigate always to Tasks (or always Config)** — rejected: yanking the user out of their chosen View is disorienting; active-View-first keeps search subordinate to the View preference.
- **Persistent or per-query index** — rejected: corpus too small to warrant persistence; per-query scans of description text would still be fast but rebuild-on-change is simpler to reason about.
