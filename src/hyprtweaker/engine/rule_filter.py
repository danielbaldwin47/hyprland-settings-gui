"""The Rules list's filter: free text plus one chip per prop and effect in use (#113).

ADR-0008's filter bar. The chip vocabulary is derived from the live rule list, never a
static catalogue: a chip exists because some rule carries that match prop or effect, so
clicking it can never produce an empty list on its own, and a plugin's prop gets a chip
the catalog has never heard of. Chips and the free text narrow together (a rule must pass
both), and the filter answers with positions in the whole list, because position is a
rule's identity (ADR-0008) and every edit addresses it.

Toolkit-free, so it is typed and tested without GTK.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from hyprtweaker.engine.model.entities import LayerRule, WindowRule
from hyprtweaker.engine.rules_catalog import match_props, prop_title

Rule = WindowRule | LayerRule


class ChipGroup(enum.Enum):
    """Which half of a rule a chip filters on."""

    MATCH = "match"
    EFFECT = "effect"


@dataclass(frozen=True, slots=True)
class Chip:
    """One filter chip: the rules whose Match names a prop, or whose Effects set one."""

    group: ChipGroup
    name: str

    @property
    def title(self) -> str:
        return prop_title(self.name)

    def holds(self, rule: Rule) -> bool:
        pool = rule.match if self.group is ChipGroup.MATCH else rule.effects
        return self.name in pool


def value_text(value: object) -> str:
    """A match or effect value as summary text -- readable, never round-tripped."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def filter_haystack(rule: Rule) -> str:
    """Everything the free-text filter matches against: label, match, effects (ADR-0008)."""
    words = [rule.name]
    for name, value in rule.match.items():
        words.append(name)
        words.append(value_text(value))
    for name, value in rule.effects.items():
        words.append(name)
        words.append(value_text(value))
    return " ".join(word for word in words if word).lower()


def chips_for(kind: str, rules: Sequence[Rule]) -> tuple[Chip, ...]:
    """The chips the list offers: match props first, then effects.

    Match props follow the catalog's order (class before title, the order the editor's
    picker uses) with props the catalog does not know after them; effects are
    alphabetical, because dozens can be in use and a user scanning for one needs a rule
    they can predict.
    """
    matched = {name for rule in rules for name in rule.match}
    effected = {name for rule in rules for name in rule.effects}
    catalog = [prop.name for prop in match_props(kind)]
    known = [name for name in catalog if name in matched]
    unknown = sorted(matched.difference(catalog))
    return (
        *(Chip(ChipGroup.MATCH, name) for name in (*known, *unknown)),
        *(Chip(ChipGroup.EFFECT, name) for name in sorted(effected)),
    )


def filter_rules(rules: Sequence[Rule], text: str, chips: Collection[Chip]) -> list[int]:
    """Positions in `rules` of the rules passing every chip and containing `text`."""
    needle = text.strip().lower()
    return [
        index
        for index, rule in enumerate(rules)
        if all(chip.holds(rule) for chip in chips)
        and (not needle or needle in filter_haystack(rule))
    ]
