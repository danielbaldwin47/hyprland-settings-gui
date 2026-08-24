"""The Trigger grammar: parsing, validation, and recording one from real input.

Toolkit-free on purpose. The tricky half of Capture is not the widget -- it is the
held-modifier bookkeeping and the question "would this string actually bind?", and both
are miserable to test through a widget tree. `ui/rows/gesture.py` set the precedent: keep
the state machine pure, let the dialog be a thin wire between GTK events and this module.

Two rules here are load-bearing and neither is obvious:

**Keysym validation must be case-insensitive.** Hyprland resolves key names through
`xkb_keysym_from_name(name, XKB_KEYSYM_CASE_INSENSITIVE)`, so `escape`, `SPACE` and
`print` are all real binds. GDK's `keyval_from_name` is case-*sensitive* and calls those
three dead -- validating through GDK would reject triggers the compositor accepts. We ask
the same library with the same flag, via `importer.keysyms`.

**A dead keysym is silently accepted by the compositor.** Probed against 0.56.2:
`bind SUPER ALT CTRL SHIFT, notakey, exec, true` returns `ok`, raises no `configerrors`,
and appears in `hyprctl binds` with `keycode: 0` -- a bind that can never fire, with no
error anywhere to find it by. Under Lua it is worse: a bind-time config error that takes
the rest of the file with it (ADR-0007, prototypes/importer/FINDINGS.md §4.4). That
asymmetry is why Capture blocks dead names at the point of entry rather than at save.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from hyprtweaker.engine.importer.keysyms import known_keysym

__all__ = [
    "CATCHALL",
    "MODIFIERS",
    "CaptureRecorder",
    "Severity",
    "Trigger",
    "TriggerProblem",
    "button_token",
    "format_trigger",
    "normalise_keysym",
    "parse_trigger",
    "validate_trigger",
    "wheel_token",
]

#: Canonical modifier spellings, in the order Hyprland's own docs list them (ADR-0007).
#: A Trigger always emits its modifiers in this order, so two captures of the same chord
#: produce byte-identical strings and golden tests stay meaningful.
MODIFIERS: tuple[str, ...] = ("SHIFT", "CAPS", "CTRL", "ALT", "MOD2", "MOD3", "SUPER", "MOD5")

#: Spellings `parseKeyString` accepts -> the canonical one. Hyprland matches these
#: case-sensitively as written, but we fold case before lookup so hand-typed `super`
#: normalises rather than being read as a keysym.
MOD_ALIASES: dict[str, str] = {
    "SHIFT": "SHIFT",
    "CAPS": "CAPS",
    "CTRL": "CTRL",
    "CONTROL": "CTRL",
    "ALT": "ALT",
    "MOD1": "ALT",
    "MOD2": "MOD2",
    "MOD3": "MOD3",
    "SUPER": "SUPER",
    "WIN": "SUPER",
    "LOGO": "SUPER",
    "MOD4": "SUPER",
    "META": "SUPER",
    "MOD5": "MOD5",
}

#: Wheel directions. The naming trap: Hyprland's `mouse_up`/`mouse_down` are *wheel*
#: directions, not button press/release.
WHEEL: frozenset[str] = frozenset({"mouse_up", "mouse_down", "mouse_left", "mouse_right"})

CATCHALL = "catchall"

#: Prefixed forms that are not xkb names, so the keysym validator must not see them.
#: `code:` is here but *not* in `_EXCLUSIVE_PREFIXES`: `parseKeyString` puts it in the
#: "one or more keysyms" branch alongside real keysym names, so `code:36 + code:37` is a
#: legal multi-key bind while `mouse:272 + Q` is not.
_SPECIAL_PREFIXES: tuple[str, ...] = ("mouse:", "switch:", "code:")

#: The syms `parseKeyString` refuses to combine with any other key. Wheel directions are
#: exclusive too, and are matched by exact name rather than prefix.
_EXCLUSIVE_PREFIXES: tuple[str, ...] = ("mouse:", "switch:")

#: Names people reach for that xkb does not know, and what they meant.
#:
#: Every entry is checked both ways by `test_triggers.py`: the key must be a name xkb
#: rejects and the value one it accepts, so this cannot drift into a table of guesses --
#: the same invariant, and the same reason, as `importer.binds._KEY_RENAMES`. A suggestion
#: that named a keysym as dead as the one it replaced would be worse than staying silent.
_SUGGESTIONS: dict[str, str] = {
    "enter": "Return",
    "esc": "Escape",
    "pgup": "Prior",
    "pgdn": "Next",
    "pageup": "Prior",
    "pagedown": "Next",
    "del": "Delete",
    "ins": "Insert",
    "capslock": "Caps_Lock",
    "printscreen": "Print",
    "windows": "Super_L",
}

#: GDK button number -> the evdev code Hyprland spells `mouse:N`. Linux `BTN_LEFT` is
#: 0x110 = 272; right and middle are *swapped* relative to GDK's 2/3 ordering, which is
#: the kind of off-by-one that silently binds the wrong button.
_BUTTON_CODES: dict[int, int] = {
    1: 272,  # BTN_LEFT
    2: 274,  # BTN_MIDDLE  (GDK 2 is middle, evdev 274)
    3: 273,  # BTN_RIGHT   (GDK 3 is right, evdev 273)
    4: 275,  # BTN_SIDE
    5: 276,  # BTN_EXTRA
}

#: Keysym names that *are* modifiers, mapped to the canonical modifier they contribute.
#: Tracked by name rather than by GDK's modifier bitmask because the bitmask reports the
#: state *before* the current press -- so a chord that ends on the modifier itself, and
#: any press with no non-modifier key at all, is invisible to it.
_MODIFIER_KEYSYMS: dict[str, str] = {
    "Shift_L": "SHIFT",
    "Shift_R": "SHIFT",
    "Control_L": "CTRL",
    "Control_R": "CTRL",
    "Alt_L": "ALT",
    "Alt_R": "ALT",
    "Meta_L": "ALT",
    "Meta_R": "ALT",
    "Super_L": "SUPER",
    "Super_R": "SUPER",
    "Hyper_L": "SUPER",
    "Hyper_R": "SUPER",
    "Caps_Lock": "CAPS",
    "ISO_Level3_Shift": "MOD5",
    "ISO_Level5_Shift": "MOD3",
    "Mode_switch": "MOD5",
}


#: Keysyms GDK reports that are an artefact of the modifier that produced them, mapped to
#: the name the user pressed. `ISO_Left_Tab` is what Shift+Tab reports; `Sys_Req` is what
#: Alt+Print reports. Binding the artefact would produce a bind that only fires under a
#: modifier combination the user never sees named (libadwaita-patterns.md §3).
_KEYSYM_NORMALISATIONS: dict[str, str] = {
    "ISO_Left_Tab": "Tab",
    "Sys_Req": "Print",
    "ISO_Enter": "Return",
}


def normalise_keysym(name: str) -> str:
    """The name to bind for a keysym GDK reported.

    Single ASCII letters are upper-cased for the canonical `"SUPER + Q"` spelling the
    corpus uses. Safe because Hyprland resolves keysym names case-insensitively -- `Q`
    and `q` bind the same physical key -- so this changes how a trigger reads, never
    which key it catches.
    """
    stripped = name.strip()
    if not stripped:
        return ""
    stripped = _KEYSYM_NORMALISATIONS.get(stripped, stripped)
    if len(stripped) == 1 and stripped.isascii() and stripped.isalpha():
        return stripped.upper()
    return stripped


class Severity(Enum):
    """Whether a problem stops the trigger or merely mentions something."""

    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class TriggerProblem:
    """One thing wrong (or worth saying) about a trigger string."""

    severity: Severity
    message: str
    hint: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.BLOCK

    def full_text(self) -> str:
        return f"{self.message} {self.hint}".strip() if self.hint else self.message


@dataclass(frozen=True, slots=True)
class Trigger:
    """A parsed Trigger: canonical modifiers plus exactly one key token.

    `keys` on a `Bind` is the flat string; this is the structured form the ADR asks the
    editor to think in, so mods can be reordered and the key swapped without string
    surgery.
    """

    mods: tuple[str, ...] = ()
    key: str = ""

    def __str__(self) -> str:
        return format_trigger(self.mods, self.key)

    @property
    def is_keycode(self) -> bool:
        return self.key.startswith("code:")

    def display(self) -> str:
        """Human-facing spelling: `code:36` reads as "key code 36"."""
        key = f"key code {self.key[5:]}" if self.is_keycode else self.key
        return " + ".join((*self.mods, key)) if key else " + ".join(self.mods)


def format_trigger(mods: tuple[str, ...] | list[str], key: str) -> str:
    """Canonical `"SUPER + SHIFT + Q"`, modifiers in `MODIFIERS` order, deduplicated."""
    ordered = [m for m in MODIFIERS if m in set(mods)]
    return " + ".join([*ordered, key]) if key else " + ".join(ordered)


def button_token(gdk_button: int) -> str:
    """GDK button number -> `mouse:N`.

    Buttons past the mapped five extrapolate from `BTN_LEFT` (272 = GDK 1), which is how
    evdev numbers the extra buttons on a gaming mouse. A guess, but a checkable one: the
    user sees the code in the trigger and the bind either fires or it does not -- better
    than refusing to capture a button the kernel is perfectly happy to report.
    """
    return f"mouse:{_BUTTON_CODES.get(gdk_button, 271 + gdk_button)}"


def wheel_token(direction: str) -> str:
    """A GDK scroll direction name (`up`/`down`/`left`/`right`) -> `mouse_up` etc."""
    return f"mouse_{direction.lower()}"


def _is_exclusive(token: str) -> bool:
    """Whether this key token refuses to share a trigger with any other key."""
    lowered = token.lower()
    return lowered in WHEEL or lowered == CATCHALL or lowered.startswith(_EXCLUSIVE_PREFIXES)


def parse_trigger(text: str) -> Trigger:
    """Split a trigger string into modifiers and key. Does not validate the key."""
    tokens = [t.strip() for t in text.split("+")]
    tokens = [t for t in tokens if t]
    mods: list[str] = []
    keys: list[str] = []
    for token in tokens:
        canonical = MOD_ALIASES.get(token.upper())
        # A modifier name only counts as a modifier while no key has been seen yet;
        # `parseKeyString` requires modifiers to precede keys, so `Q + SHIFT` has two
        # keys, not a modifier -- and reporting that honestly is what lets validation
        # explain it.
        if canonical is not None and not keys:
            if canonical not in mods:
                mods.append(canonical)
        else:
            keys.append(token)
    return Trigger(tuple(m for m in MODIFIERS if m in set(mods)), " + ".join(keys))


def validate_trigger(text: str, *, in_submap: bool = False) -> TriggerProblem | None:
    """The worst thing wrong with this trigger string, or None if it is fine.

    Deliberately looser than GNOME's shortcut validation, per ADR-0007: modifier-less
    binds are legal in Hyprland, and a bare unmodified letter is a real (if usually
    unwise) bind -- so that warns rather than blocks. Only what would genuinely not fire,
    or would break the config, blocks.
    """
    stripped = text.strip()
    if not stripped:
        return TriggerProblem(Severity.BLOCK, "Press a key combination.")

    trigger = parse_trigger(stripped)
    key = trigger.key

    if not key:
        return TriggerProblem(
            Severity.BLOCK,
            "That is only modifiers.",
            "Hold the modifiers and press the key you want to bind.",
        )

    lowered = key.lower()

    if lowered == CATCHALL:
        if trigger.mods:
            return TriggerProblem(
                Severity.BLOCK,
                "catchall cannot carry modifiers.",
                "Hyprland rejects the whole config with "
                'Unknown keysym: "catchall". Use catchall on its own.',
            )
        if not in_submap:
            return TriggerProblem(
                Severity.WARN,
                "catchall only does anything inside a submap.",
                "In the root config it catches nothing.",
            )
        return None

    # Special syms are exclusive: `parseKeyString` will not combine one with another key.
    if " + " in key:
        parts = [p.strip() for p in key.split("+") if p.strip()]
        if any(_is_exclusive(p) for p in parts):
            return TriggerProblem(
                Severity.BLOCK,
                "Mouse, wheel and switch triggers cannot be combined with other keys.",
                f"Use just one of: {', '.join(p for p in parts if _is_exclusive(p))}.",
            )
        return TriggerProblem(
            Severity.WARN,
            "Multi-key binds are shown as written and cannot be captured.",
            "Edit the text directly if this is what you meant.",
        )

    if lowered in WHEEL:
        return None

    if lowered.startswith("mouse:"):
        return _validate_numeric(key, "mouse:", "a button code")
    if lowered.startswith("code:"):
        return _validate_numeric(key, "code:", "a key code")
    if lowered.startswith("switch:"):
        name = key.split(":", 1)[1]
        name = name.split(":", 1)[1] if name.lower().startswith(("on:", "off:")) else name
        if not name.strip():
            return TriggerProblem(
                Severity.BLOCK,
                "That switch trigger has no switch name.",
                "Use switch:<name>, spelled exactly as your device list reports it.",
            )
        return None

    known = known_keysym(key)
    if known is False:
        suggestion = _SUGGESTIONS.get(lowered)
        hint = f"Did you mean {suggestion}?" if suggestion else "Try capturing it instead."
        return TriggerProblem(
            Severity.BLOCK,
            f"{key!r} is not a key name xkb knows, so this bind would never fire "
            "-- and Lua rejects the whole config rather than ignoring it.",
            hint,
        )
    if known is None:
        # No validator on this machine. Saying nothing is right: a guess here would file
        # false errors against perfectly good triggers.
        return None

    if not trigger.mods and len(key) == 1 and key.isalnum():
        return TriggerProblem(
            Severity.WARN,
            f"{key} on its own will fire whenever you type it.",
            "Add a modifier unless you meant that.",
        )
    return None


def _validate_numeric(key: str, prefix: str, label: str) -> TriggerProblem | None:
    raw = key[len(prefix) :].strip()
    if not raw.isdigit():
        return TriggerProblem(
            Severity.BLOCK,
            f"{prefix}{raw} is not {label}.",
            f"Use {prefix}<number>.",
        )
    return None


@dataclass(slots=True)
class CaptureRecorder:
    """Held-modifier bookkeeping for the Capture dialog.

    Fed keysym *names*, GDK button numbers and scroll directions, so it stays free of
    GTK types and testable without a display. Modifiers are tracked by press/release
    rather than read from GDK's modifier bitmask, because that bitmask reflects the state
    before the current event -- HyprMod hit the same wall (libadwaita-patterns.md §3).
    """

    held: list[str] = field(default_factory=list)
    _latched: tuple[str, ...] = ()

    def reset(self) -> None:
        self.held.clear()
        self._latched = ()

    @property
    def mods(self) -> tuple[str, ...]:
        """Currently-held modifiers, canonically ordered."""
        current = set(self.held)
        return tuple(m for m in MODIFIERS if m in current)

    def press(self, keysym: str, keycode: int = 0) -> Trigger | None:
        """A key went down. Returns a Trigger once a non-modifier key settles it.

        A modifier press returns None -- the chord is not finished yet -- but is latched,
        so releasing modifiers before the dialog reads them does not lose the chord.
        """
        modifier = _MODIFIER_KEYSYMS.get(keysym)
        if modifier is not None:
            if modifier not in self.held:
                self.held.append(modifier)
            self._latched = self.mods
            return None

        mods = self.mods or self._latched
        name = normalise_keysym(keysym)
        # An unnamed keyval still has hardware behind it: bind the code rather than
        # refusing, which is the only way a key with no keysym on this layout is bindable
        # at all. `code:N` is the X11-style keycode (evdev + 8) that GDK already reports,
        # so Return's evdev 28 arrives as the 36 the ADR shows.
        if (not name or known_keysym(name) is False) and keycode:
            return Trigger(mods, f"code:{keycode}")
        return Trigger(mods, name)

    def release(self, keysym: str) -> None:
        modifier = _MODIFIER_KEYSYMS.get(keysym)
        if modifier is not None and modifier in self.held:
            self.held.remove(modifier)

    def button(self, gdk_button: int) -> Trigger:
        """A mouse button went down."""
        return Trigger(self.mods or self._latched, button_token(gdk_button))

    def wheel(self, direction: str) -> Trigger:
        """The wheel turned."""
        return Trigger(self.mods or self._latched, wheel_token(direction))

    def modifier_only(self) -> Trigger | None:
        """The chord as it stands, if only modifiers are held."""
        mods = self.mods
        return Trigger(mods, "") if mods else None
