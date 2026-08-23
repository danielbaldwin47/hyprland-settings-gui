"""The Options half of the model: tri-state, schema-checked, ordered by Hyprland.

`ConfigModel` is the single in-memory truth for every `hl.config` value (ADR-0005). Its
one structural idea is the **tri-state**:

- **Unset** -- the model holds nothing. The writer emits nothing, and because a reload
  resets every value to its default first, omission *is* "Hyprland's default". This is
  what a Row's reset button produces.
- **Set to a value** -- always emitted, *even when it equals the current default*. That is
  the whole point of the distinction: a user who deliberately picks `rounding = 10` keeps
  10 when upstream changes the default, while a user who never touched it follows upstream.
- **Set to null** -- an explicit "no value" for the 26 nullable Options, emitted as the
  curated `null_value` (`""`, `-1`). Different from Unset: `input:kb_variant` unset lets
  Hyprland decide, while set-to-null writes an empty variant into the config.

Values are parsed and type-checked on the way in (`values.parse_value`), so an Option can
only ever hold a value of its own type. Bounds are deliberately *not* enforced: the Row's
control is what makes an out-of-range value unenterable (ADR-0013), and a model that
clamped would silently rewrite a value an importer read from a working config.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from typing import Any, Final

from ..schema import ResolvedOption, Schema
from .entities import EntitySet
from .values import display_text, has_emittable_null, parse_value


class _Unset(enum.Enum):
    """A singleton sentinel type, so `UNSET` is narrowable by mypy and `None` stays free.

    `None` already means something here -- explicit null -- so "absent" needs its own
    object rather than the usual `None` default.
    """

    TOKEN = enum.auto()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset.TOKEN
"""The Option is not in the model: nothing is emitted, Hyprland's default applies."""

OptionValue = Any
"""A concrete value, `None` for explicit null, or `UNSET`."""


class UnknownOption(KeyError):
    """A name no shipped Schema knows.

    Loud rather than tolerated: a typo'd key writes a Module that fails the whole reload
    with `unknown config key`, taking every other value in the file down with it.
    """


class NotNullable(ValueError):
    """`set_null` on an Option with no curated `null_value` to emit."""


class ConfigModel:
    """Every Option value the app holds, checked against one resolved Schema."""

    def __init__(self, schema: Schema) -> None:
        self._schema = schema
        self._values: dict[str, Any] = {}
        self._entities = EntitySet()

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def entities(self) -> EntitySet:
        """The non-Option half of the config -- Binds, Rules, monitor rules (ADR-0007).

        Held here, beside the Option values, because "the model" is what the Writer renders
        and what the Apply transaction applies, and an Entity that lived somewhere else
        would need every one of those seams widened to carry it. Mutable and edited in
        place: for Binds and Rules position *is* identity, so reordering the list is the
        edit, not a re-keying.

        Empty for a model nobody has imported into, which is why attaching it here changes
        nothing for existing callers -- an empty `EntitySet` renders no Modules at all.
        """
        return self._entities

    def adopt_entities(self, entities: EntitySet) -> None:
        """Replace the Entity half wholesale -- what an Importer hands back (ADR-0009)."""
        self._entities = entities

    # --- reading ------------------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self._values

    def __len__(self) -> int:
        return len(self._values)

    def get(self, name: str) -> OptionValue:
        """The value, `None` for explicit null, or `UNSET`."""
        self._option(name)
        return self._values.get(name, UNSET)

    def is_set(self, name: str) -> bool:
        """True for both "set to a value" and "set to null" -- i.e. "the writer emits it"."""
        self._option(name)
        return name in self._values

    def display(self, name: str) -> str | None:
        """The display-text representation, or `None` when unset or explicitly null."""
        value = self.get(name)
        if value is UNSET or value is None:
            return None
        return display_text(value)

    def option(self, name: str) -> ResolvedOption:
        """The Schema record behind a name, for callers that have the name but not the row."""
        return self._option(name)

    # --- writing ------------------------------------------------------------------------

    def set(self, name: str, value: Any) -> None:
        """Set an Option, parsing `value` into the Option's own type first.

        Passing `None` means explicit null and routes to `set_null`, so a UI that binds a
        nullable control to one setter does not have to branch.
        """
        option = self._option(name)
        if value is None:
            self.set_null(name)
            return
        self._values[name] = parse_value(option.type, self._resolve_enum(option, value))

    def set_null(self, name: str) -> None:
        """Explicitly set an Option to "no value" -- emitted as its curated `null_value`.

        For the handful of Options whose null cannot be spelled in Lua at all (the colour
        and gradient fallbacks, `has_emittable_null`), "no value" *is* absence, so this
        unsets instead. The user gesture is the same either way -- picking "Same as shadow
        colour" -- and both paths produce exactly the fallback that label promises.
        """
        option = self._option(name)
        if not option.nullable:
            raise NotNullable(
                f"{name} is not nullable; unset it instead to fall back to Hyprland's default"
            )
        if not has_emittable_null(option):
            self.unset(name)
            return
        self._values[name] = None

    def unset(self, name: str) -> None:
        """Remove an Option from the model. Idempotent -- resetting twice is not an error."""
        self._option(name)
        self._values.pop(name, None)

    def update(self, values: dict[str, Any]) -> None:
        """Set many Options at once, e.g. from an importer or a Preset."""
        for name, value in values.items():
            self.set(name, value)

    def clear(self) -> None:
        self._values.clear()

    # --- iteration, in Hyprland's own declaration order ---------------------------------

    def set_options(self) -> tuple[tuple[ResolvedOption, OptionValue], ...]:
        """Every set Option with its value, ordered as Hyprland declares them.

        Declaration order, never insertion order: the writer's output has to depend on the
        model's *content* alone, or two identical configs would hash differently purely
        because the user clicked them in a different sequence.
        """
        return tuple(
            (option, self._values[option.name])
            for option in sorted(
                (self._schema[name] for name in self._values),
                key=lambda option: option.order,
            )
        )

    def sections(self) -> tuple[str, ...]:
        """The Sections with at least one set Option -- exactly the Modules to write."""
        seen: dict[str, None] = {}
        for option, _ in self.set_options():
            seen.setdefault(option.section, None)
        return tuple(seen)

    def section(self, section: str) -> tuple[tuple[ResolvedOption, OptionValue], ...]:
        return tuple(item for item in self.set_options() if item[0].section == section)

    def __iter__(self) -> Iterator[tuple[ResolvedOption, OptionValue]]:
        return iter(self.set_options())

    # --- internals ----------------------------------------------------------------------

    def _option(self, name: str) -> ResolvedOption:
        option = self._schema.get(name)
        if option is None:
            raise UnknownOption(
                f"no Option named {name!r} in Hyprland {self._schema.hyprland_version}"
            )
        return option

    @staticmethod
    def _resolve_enum(option: ResolvedOption, value: Any) -> Any:
        """Turn an enum-mapped int Option's *name* into its number.

        `descriptions` ships the map (`{"dwindle": 0, ...}`) and both a `.conf` line and a
        combo row naturally speak the name, so the model accepts either. Anything not in
        the map falls through to the type parser, which will reject a genuine typo.
        """
        if option.map and isinstance(value, str) and value in option.map:
            return option.map[value]
        return value
