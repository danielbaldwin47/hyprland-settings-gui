"""Ask the compositor what each `hl.dsp.*` dispatcher takes (#126, ADR-0007).

Hyprland's Lua stub types every dispatcher as `fun(...)`, so the catalog's argument shapes
cannot be read from anywhere machine-readable and have to be asked of the binary. This module
asks, on a nested compositor, and writes the answer down as a record a person can diff
between Hyprland versions (`tests/golden/dispatcher-probe-<version>.json`).

**What a probe can and cannot see.** Hyprland 0.56.2 validates a dispatcher's arguments when
the factory is called, so `eval hl.dsp.X(...)` answers `ok` or an error without running
anything. That is a strong oracle for some things and silent on others:

- *Required keys*: `hl.dsp.X({})` names every missing one (`'mods' is required`).
- *The compositor's own shape*: `hl.dsp.X()` says `expected a table { mods, key, window? }`,
  and a one-of dispatcher lists its alternatives. Those names are the hint keys.
- *Keys it reads*: an unknown key is **silently ignored** (`window.close{foo = 1}` is `ok`),
  so a key cannot be proven by being accepted. It can be proven by being *read*: a value of
  the wrong type for a key the dispatcher parses raises, a value for one it ignores does not
  (`{ window = {} }` raises `expected a window object or selector`, `{ foo = {} }` does not).
  That works for window, monitor, workspace, direction and the numeric keys.
- *Keys it reads but does not check at construction* (`action`, the booleans, a positional
  name) only show at fire time, so a few small effect probes open `foot` windows in the
  nested compositor, fire the dispatcher and read the compositor's state back (`clients`,
  `monitors`, `workspaces`, `activewindow`).

Group-only keys (`forward`, the `action` of the lock and deny dispatchers) are fired at a
group of two or three windows and read back from the group's member order and from whether a
fourth window can join (a locked or denying group refuses it). A key neither route confirms stays out
of the record, and a catalog entry that names one, or omits one the record holds, is a
disagreement (`check_entry`). The keys that were fired and did nothing (`window` on the group
dispatchers, `layout_aware` on the fullscreen ones) are not in the record, which holds only
what acted; `probe_unseen` fires them again and `test_the_unseen_keys_still_do_nothing` fails
the day one starts to act, which is the day the catalog can give it a row. The editor keeps
the saved copy of a key without a row, so a curated entry lists what acted and loses nothing.

Everything here goes through the `NestedHyprland` it is handed, never `hyprctl` on its own
and never the session's compositor: `nested.hyprctl_text` and `nested.dispatch` run with the
nested instance's environment.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_dispatcher_probe.py \\
        -m hyprland
    UPDATE_GOLDEN=1 ...   # rewrites the record; read the diff
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hyprtweaker.engine.dispatchers import CATALOG, ArgSpec, Dispatcher

if TYPE_CHECKING:
    from .nested import NestedHyprland

PROBE_STRING = "'probe'"
WRONG_VALUES = ("{}", "'zzz'")
"""Wrong for any parsed key: a table fails every scalar reader, `zzz` fails numbers, enums
and directions."""

KEYS = (
    "window",
    "action",
    "monitor",
    "workspace",
    "direction",
    "index",
    "x",
    "y",
    "relative",
    "follow",
    "mods",
    "key",
    "state",
    "corner",
    "mode",
    "signal",
    "prop",
    "value",
    "name",
    "id",
    "next",
    "prev",
    "tiled",
    "floating",
    "forward",
    "keep_aspect_ratio",
    "layout_aware",
    "internal",
    "client",
    "monitor1",
    "monitor2",
    "group_aware",
    "on_current_monitor",
    "target",
    "with",
    "other",
    "urgent_or_last",
    "last",
    "into_group",
    "out_of_group",
    "into_or_create_group",
    "seconds",
)
"""Every key name any dispatcher might read, gathered from the compositor's own messages, the
research table (`docs/research/lua-api-surface.md` §4a) and what the Importer emits. A
candidate list, not a claim: the probe keeps only the ones the compositor reads."""

SAMPLE_VALUES: dict[str, str] = {
    "window": "'activewindow'",
    "action": "'toggle'",
    "monitor": "'DP-1'",
    "monitor1": "'DP-1'",
    "monitor2": "'DP-2'",
    "workspace": "1",
    "direction": "'left'",
    "mods": "'SUPER'",
    "key": "'a'",
    "state": "'down'",
    "mode": "'top'",
    "prop": "'opaque'",
    "value": "'1'",
    "name": "'probe'",
    "tag": "'probe'",
    "command": "'true'",
}
"""A value the compositor accepts for a key, by name. Typed samples (`SAMPLE_BY_TYPE`) cover
the rest."""

PATH_SAMPLES: dict[tuple[str, str], str] = {("window.fullscreen", "mode"): "'maximized'"}
"""Where one dispatcher takes a different literal for a key than `SAMPLE_VALUES` says."""

SAMPLE_BY_TYPE: dict[str, str] = {
    "string": PROBE_STRING,
    "int": "1",
    "bool": "true",
    "window": "'activewindow'",
    "workspace": "1",
    "direction": "'left'",
}


@dataclass(frozen=True, slots=True)
class Plan:
    """How to probe one dispatcher: the smallest table call it accepts."""

    base: tuple[str, ...] = ()
    """`key = value` fields of a table call the compositor takes; empty is `{}`."""


def _plans() -> dict[str, Plan]:
    plain = Plan()
    window = Plan(("window = 'activewindow'",))
    return {
        # --- root: the ones that were never free_form, so the whole catalog has a record ---
        "exec_cmd": plain,
        "exec_raw": plain,
        "submap": plain,
        "global": plain,
        "exit": plain,
        "force_renderer_reload": plain,
        "no_op": plain,
        "release_input_capture": plain,
        # --- root ---
        "pass": window,
        "force_idle": plain,
        "dpms": plain,
        "event": plain,
        "focus": window,
        "layout": plain,
        "send_key_state": Plan(("mods = 'SUPER'", "key = 'a'", "state = 'down'")),
        "send_shortcut": Plan(("mods = 'SUPER'", "key = 'a'")),
        # --- cursor ---
        "cursor.move": Plan(("x = 1", "y = 1")),
        "cursor.move_to_corner": Plan(("corner = 1",)),
        # --- group ---
        "group.toggle": plain,
        "group.lock": plain,
        "group.lock_active": plain,
        "group.next": plain,
        "group.prev": plain,
        "group.active": Plan(("index = 1",)),
        "group.move_window": plain,
        # --- window ---
        "window.close": plain,
        "window.kill": plain,
        "window.center": plain,
        "window.clear_tags": plain,
        "window.deny_from_group": plain,
        "window.fullscreen": plain,
        "window.signal": Plan(("signal = 9",)),
        "window.tag": Plan(("tag = 'probe'",)),
        "window.float": plain,
        "window.pin": plain,
        "window.pseudo": plain,
        "window.bring_to_top": plain,
        "window.toggle_swallow": plain,
        "window.alter_zorder": Plan(("mode = 'top'",)),
        "window.cycle_next": plain,
        "window.drag": plain,
        "window.fullscreen_state": Plan(("internal = 1", "client = 1")),
        "window.move": Plan(("direction = 'left'",)),
        "window.resize": Plan(("x = 1", "y = 1")),
        "window.set_prop": Plan(("prop = 'opaque'", "value = '1'")),
        "window.swap": Plan(("direction = 'left'",)),
        # --- workspace ---
        "workspace.change_id": Plan(("workspace = 1", "id = 2")),
        "workspace.move": Plan(("monitor = 'DP-1'",)),
        "workspace.rename": Plan(("workspace = 1",)),
        "workspace.swap_monitors": Plan(("monitor1 = 'DP-1'", "monitor2 = 'DP-2'")),
        "workspace.toggle_special": plain,
    }


PLANS: dict[str, Plan] = _plans()
"""Every dispatcher in the catalog: the 23 that were `free_form` (#126), the seven the research
table disagreed with, and the rest, so a record covers the whole catalog and a release check
has one thing to diff."""


# --- the record ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FormResult:
    """One call form and the compositor's answer to it."""

    label: str
    lua: str
    accepted: bool
    error: str


@dataclass(frozen=True, slots=True)
class EffectResult:
    """A key proven by firing the dispatcher and reading the compositor's state back."""

    path: str
    key: str
    confirmed: bool
    observed: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Verdict:
    """A key that was fired and read back, and whether anything changed. Not in the record."""

    path: str
    key: str
    acted: bool
    observed: dict[str, Any]


@dataclass(slots=True)
class DispatcherRecord:
    """Everything the probe learned about one dispatcher."""

    path: str
    forms: list[FormResult] = field(default_factory=list)
    required_keys: tuple[str, ...] = ()
    hint_keys: tuple[str, ...] = ()
    hint_optional_keys: tuple[str, ...] = ()
    read_keys: tuple[str, ...] = ()
    confirmed_keys: tuple[str, ...] = ()

    def form(self, label: str) -> FormResult:
        return next(result for result in self.forms if result.label == label)

    @property
    def known_keys(self) -> frozenset[str]:
        """Every key the compositor proved it reads, by any route."""
        return frozenset(
            (*self.read_keys, *self.hint_keys, *self.hint_optional_keys, *self.confirmed_keys)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "forms": [
                {"label": f.label, "lua": f.lua, "accepted": f.accepted, "error": f.error}
                for f in self.forms
            ],
            "required_keys": list(self.required_keys),
            "hint_keys": list(self.hint_keys),
            "hint_optional_keys": list(self.hint_optional_keys),
            "read_keys": list(self.read_keys),
            "confirmed_keys": list(self.confirmed_keys),
        }

    @classmethod
    def from_json(cls, path: str, data: dict[str, Any]) -> DispatcherRecord:
        return cls(
            path=path,
            forms=[FormResult(**form) for form in data["forms"]],
            required_keys=tuple(data["required_keys"]),
            hint_keys=tuple(data["hint_keys"]),
            hint_optional_keys=tuple(data["hint_optional_keys"]),
            read_keys=tuple(data["read_keys"]),
            confirmed_keys=tuple(data["confirmed_keys"]),
        )


@dataclass(slots=True)
class ProbeRecord:
    """The probe's whole answer: diff it between Hyprland versions."""

    hyprland_version: str
    dispatchers: dict[str, DispatcherRecord] = field(default_factory=dict)
    effects: list[EffectResult] = field(default_factory=list)
    effects_skipped: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "hyprland_version": self.hyprland_version,
            "dispatchers": {p: r.to_json() for p, r in sorted(self.dispatchers.items())},
            "effects": [
                {"path": e.path, "key": e.key, "confirmed": e.confirmed, "observed": e.observed}
                for e in self.effects
            ],
            "effects_skipped": self.effects_skipped,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ProbeRecord:
        return cls(
            hyprland_version=data["hyprland_version"],
            dispatchers={
                p: DispatcherRecord.from_json(p, r) for p, r in data["dispatchers"].items()
            },
            effects=[EffectResult(**e) for e in data["effects"]],
            effects_skipped=data["effects_skipped"],
        )


# --- reading the compositor's answers --------------------------------------------------

_REQUIRED = re.compile(r"'(\w+)' (?:and '(\w+)' )?(?:is|are) required")
_HINT = re.compile(r"expected a table\s*\{([^}]*)\}")
_ONE_OF = re.compile(r"Expected (?:one of: |positions \(x & y\) or )([^|]*)")


def clean(reply: str) -> tuple[bool, str]:
    """`(accepted, error text)` for one `eval` reply.

    Several errors arrive as several `error:` lines; they are joined with ` | ` so a record
    holds one string per form. The `=[C]:-1:` prefix is Hyprland's, not information.
    """
    text = reply.strip()
    if text == "ok":
        return True, ""
    lines = [
        line.removeprefix("error: ").replace("=[C]:-1: ", "") for line in text.splitlines()
    ]
    return False, " | ".join(line.strip() for line in lines if line.strip())


def required_keys(error: str) -> tuple[str, ...]:
    """The keys a `{}` call reported missing, in the order Hyprland named them."""
    found: list[str] = []
    for match in _REQUIRED.finditer(error):
        found.extend(group for group in match.groups() if group)
    return tuple(dict.fromkeys(found))


def hint_keys(error: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(required, optional)` key names from a shape hint such as `{ mods, key, window? }`.

    A `{ a, b?, c }` hint is the compositor describing its own call. A one-of dispatcher has
    no such hint but says `Expected one of: direction, monitor, window`; those names come
    back as optional, since no single one is required.
    """
    required: list[str] = []
    optional: list[str] = []
    for match in _HINT.finditer(error):
        for raw in match.group(1).split(","):
            name = raw.strip()
            if not name:
                continue
            (optional if name.endswith("?") else required).append(name.rstrip("?"))
    for match in _ONE_OF.finditer(error):
        for raw in match.group(1).split(","):
            name = re.sub(r"\(.*?\)", "", raw).strip()
            optional.extend(part for part in re.split(r"[/+&]", name) if part.isidentifier())
    return tuple(dict.fromkeys(required)), tuple(dict.fromkeys(optional))


# --- the construction probe ------------------------------------------------------------


def _table(fields: Iterable[str]) -> str:
    return "{" + ", ".join(fields) + "}"


def _eval(nested: NestedHyprland, lua: str) -> tuple[bool, str]:
    return clean(nested.hyprctl_text("eval", lua))


def probe_construction(nested: NestedHyprland, path: str, plan: Plan) -> DispatcherRecord:
    """Every fixed form, then the key oracle, for one dispatcher."""
    record = DispatcherRecord(path=path)
    call = f"hl.dsp.{path}"

    def ask(label: str, lua: str) -> FormResult:
        accepted, error = _eval(nested, lua)
        result = FormResult(label, lua, accepted, error)
        record.forms.append(result)
        return result

    ask("no_args", f"{call}()")
    empty = ask("empty_table", f"{call}({{}})")
    ask("bare_string", f"{call}({PROBE_STRING})")
    ask("bare_number", f"{call}(1)")
    base = ask("base_table", f"{call}{_table(plan.base)}")

    no_args = record.form("no_args")
    record.required_keys = required_keys(empty.error)
    hinted = hint_keys(no_args.error)
    one_of = hint_keys(empty.error)
    record.hint_keys = tuple(dict.fromkeys((*hinted[0], *one_of[0])))
    record.hint_optional_keys = tuple(
        dict.fromkeys(k for k in (*hinted[1], *one_of[1]) if k not in record.hint_keys)
    )

    if base.accepted:
        record.read_keys = _read_keys(nested, call, plan)
    return record


def _read_keys(nested: NestedHyprland, call: str, plan: Plan) -> tuple[str, ...]:
    """Keys the dispatcher parses: swapping in a wrong-typed value raises."""
    read: list[str] = []
    for key in KEYS:
        kept = [f for f in plan.base if not f.startswith(f"{key} =")]
        for wrong in WRONG_VALUES:
            accepted, _ = _eval(nested, f"{call}{_table([*kept, f'{key} = {wrong}'])}")
            if not accepted:
                read.append(key)
                break
    return tuple(read)


# --- the effect probe ------------------------------------------------------------------


class Bench:
    """A nested compositor with `foot` windows to aim a dispatcher at."""

    def __init__(self, nested: NestedHyprland) -> None:
        self.nested = nested

    def open(self, *names: str) -> None:
        for name in names:
            self.nested.hyprctl_text("eval", f"hl.exec_cmd('foot -a {name} sleep 600')")
            self._until(lambda n=name: n in self.classes(), f"window {name} opening")
        time.sleep(0.5)

    def classes(self) -> list[str]:
        return [str(c.get("class")) for c in self.nested.hyprctl("clients") or []]

    def client(self, name: str) -> dict[str, Any]:
        return next(c for c in self.nested.hyprctl("clients") or [] if c.get("class") == name)

    def active(self) -> str:
        window = self.nested.hyprctl("activewindow")
        return str(window.get("class")) if isinstance(window, dict) else ""

    def fire(self, lua: str) -> None:
        self.nested.dispatch(lua)
        time.sleep(0.35)

    def focus(self, name: str) -> None:
        self.fire(f"hl.dsp.focus{{window = 'class:{name}'}}")

    def close_all(self) -> None:
        for client in self.nested.hyprctl("clients") or []:
            self.nested.dispatch(
                f"hl.dsp.window.close{{ window = 'address:{client['address']}' }}"
            )
        self._until(lambda: not self.classes(), "windows closing", required=False)

    def special(self) -> str:
        monitors = self.nested.hyprctl("monitors") or [{}]
        return str(monitors[0].get("specialWorkspace", {}).get("name", ""))

    def _until(self, ready: Callable[[], bool], what: str, *, required: bool = True) -> None:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if ready():
                return
            time.sleep(0.25)
        if required:
            raise AssertionError(f"timed out waiting for {what}")


def _toggle_family(
    bench: Bench, path: str, field_name: str, *, float_first: bool
) -> EffectResult:
    """`action = "enable"` twice then `"disable"` twice: only a parsed action holds still."""
    bench.open("pa")
    if float_first:
        bench.fire("hl.dsp.window.float{ action = 'enable', window = 'class:pa' }")
    seen = []
    for action in ("enable", "enable", "disable", "disable"):
        bench.fire(f"hl.dsp.{path}{{ action = '{action}', window = 'class:pa' }}")
        seen.append(bool(bench.client("pa")[field_name]))
    bench.close_all()
    return EffectResult(path, "action", seen == [True, True, False, False], {field_name: seen})


def _pseudo_action(bench: Bench) -> EffectResult:
    """Pseudo-tiling is in no client field; it shows as the tile shrinking to the window's own
    size, so the same four calls are read off `size` against the untouched tile."""
    bench.open("pa")
    tile = bench.client("pa")["size"]
    seen = []
    for action in ("enable", "enable", "disable", "disable"):
        bench.fire(f"hl.dsp.window.pseudo{{ action = '{action}', window = 'class:pa' }}")
        seen.append(bench.client("pa")["size"] != tile)
    bench.close_all()
    return EffectResult(
        "window.pseudo", "action", seen == [True, True, False, False], {"resized": seen}
    )


def _dpms_action(bench: Bench) -> EffectResult:
    seen = []
    for action in ("off", "off", "on", "on"):
        bench.fire(f"hl.dsp.dpms{{ action = '{action}' }}")
        seen.append(bool((bench.nested.hyprctl("monitors") or [{}])[0].get("dpmsStatus")))
    return EffectResult(
        "dpms", "action", seen == [False, False, True, True], {"dpmsStatus": seen}
    )


def _rename_name(bench: Bench) -> EffectResult:
    def name() -> str:
        spaces = bench.nested.hyprctl("workspaces") or []
        return str(next(w["name"] for w in spaces if w["id"] == 1))

    bench.fire("hl.dsp.workspace.rename{ workspace = 1, name = 'probed' }")
    named = name()
    bench.fire("hl.dsp.workspace.rename{ workspace = 1 }")
    cleared = name()
    return EffectResult(
        "workspace.rename",
        "name",
        (named, cleared) == ("probed", ""),
        {"named": named, "cleared": cleared},
    )


def _toggle_special_positional(bench: Bench) -> EffectResult:
    """The name is a bare string: `('x')` opens `special:x`, `{ name = 'x' }` is ignored."""
    bench.fire("hl.dsp.workspace.toggle_special('probed')")
    positional = bench.special()
    bench.fire("hl.dsp.workspace.toggle_special('probed')")
    bench.fire("hl.dsp.workspace.toggle_special{ name = 'probed' }")
    table = bench.special()
    bench.fire("hl.dsp.workspace.toggle_special{ name = 'probed' }")
    return EffectResult(
        "workspace.toggle_special",
        "<positional>",
        positional == "special:probed" and table != "special:probed",
        {"positional": positional, "table": table},
    )


def _cycle_next(bench: Bench) -> list[EffectResult]:
    """Three windows `pa pb pc`: each flag moves the cycle somewhere the default does not."""

    def after(from_window: str, fields: str) -> str:
        bench.focus(from_window)
        bench.fire(f"hl.dsp.window.cycle_next{{ {fields} }}")
        return bench.active()

    bench.open("pa", "pb", "pc")
    results: list[EffectResult] = []

    default = after("pa", "")
    backwards = after("pa", "next = false")
    results.append(
        EffectResult(
            "window.cycle_next",
            "next",
            backwards != default,
            {"default": default, "next_false": backwards},
        )
    )

    # One floating window, which Hyprland keeps at the end of its window list: from the last
    # tiled window the default cycle enters it and `tiled` wraps past it; from the first, the
    # default skips ahead to a tiled window and `floating` goes straight to it.
    bench.fire("hl.dsp.window.float{ window = 'class:pb' }")
    default = after("pc", "")
    tiled = after("pc", "tiled = true")
    results.append(
        EffectResult(
            "window.cycle_next", "tiled", tiled != default, {"default": default, "tiled": tiled}
        )
    )
    default = after("pa", "")
    floating = after("pa", "floating = true")
    results.append(
        EffectResult(
            "window.cycle_next",
            "floating",
            floating != default,
            {"default": default, "floating": floating},
        )
    )
    bench.close_all()
    return results


# --- the group probes ------------------------------------------------------------------


def _client_names(bench: Bench) -> dict[str, str]:
    return {str(c["address"]): str(c["class"]) for c in bench.nested.hyprctl("clients") or []}


def _group_order(bench: Bench, name: str) -> list[str]:
    """The class names of `name`'s group, in the order the compositor lists them."""
    names = _client_names(bench)
    return [names.get(a, a) for a in bench.client(name)["grouped"]]


def _group_two(bench: Bench) -> None:
    """`pa` and `pb` in one group, `pc` outside it, `pb` focused."""
    bench.open("pa", "pb", "pc")
    bench.focus("pa")
    bench.fire("hl.dsp.group.toggle{}")
    bench.focus("pb")
    bench.fire("hl.dsp.window.move{ into_group = 'left' }")


def _group_three(bench: Bench) -> None:
    """`pa`, `pb`, `pc` in one group, in that order, `pb` focused: the middle one."""
    bench.open("pa", "pb", "pc")
    bench.focus("pa")
    bench.fire("hl.dsp.group.toggle{}")
    for name in ("pb", "pc"):
        bench.focus(name)
        bench.fire("hl.dsp.window.move{ into_group = 'left' }")
    bench.focus("pb")


def _tries_to_join(bench: Bench) -> bool:
    """Whether `pc` can be moved into the group `pa` is in; if it joined, it is put back out.

    A locked group refuses a new member, and so does one whose active window is denied from
    groups, which is how those two dispatchers' `action` shows.
    """
    bench.focus("pc")
    bench.fire("hl.dsp.window.move{ into_group = 'left' }")
    joined = bool(bench.client("pc")["grouped"])
    if joined:
        bench.fire("hl.dsp.window.move{ out_of_group = true }")
    return joined


def _group_gate_action(bench: Bench, path: str) -> EffectResult:
    """`action` on a dispatcher that opens or closes a group to newcomers.

    Fired at `pa`, a member, then `pc` tries to join: `enable` twice and `disable` twice show a
    parsed action (refused, refused, joined, joined; a toggle would alternate).
    """
    _group_two(bench)
    seen = []
    for action in ("enable", "enable", "disable", "disable"):
        bench.focus("pa")
        bench.fire(f"hl.dsp.{path}{{ action = '{action}' }}")
        seen.append(not _tries_to_join(bench))
    bench.close_all()
    return EffectResult(path, "action", seen == [True, True, False, False], {"refused": seen})


def _move_window_order(bench: Bench, fields: str) -> list[str]:
    """`pb`'s group order after one `group.move_window` with `fields`, from a fresh group."""
    _group_three(bench)
    bench.fire(f"hl.dsp.group.move_window{{ {fields} }}")
    order = _group_order(bench, "pb")
    bench.close_all()
    return order


def _move_window_forward(bench: Bench) -> EffectResult:
    """From a fresh `pa pb pc` with `pb` focused: no key and `forward = true` move `pb` one
    way, `forward = false` the other."""
    default = _move_window_order(bench, "")
    forwards = _move_window_order(bench, "forward = true")
    backwards = _move_window_order(bench, "forward = false")
    return EffectResult(
        "group.move_window",
        "forward",
        backwards != default and forwards == default,
        {"default": default, "forward_true": forwards, "forward_false": backwards},
    )


def _gate_window(bench: Bench, path: str) -> Verdict:
    """Fired at `pa` with `window = 'class:pc'`: if the key aimed it, `pc` was locked or
    denied instead of the group, and it could still join."""
    _group_two(bench)
    bench.focus("pa")
    bench.fire(f"hl.dsp.{path}{{ action = 'enable', window = 'class:pc' }}")
    joined = _tries_to_join(bench)
    bench.focus("pa")
    bench.fire(f"hl.dsp.{path}{{ action = 'disable' }}")
    bench.close_all()
    return Verdict(path, "window", joined, {"pc_joined": joined})


def _group_lock_window(bench: Bench) -> Verdict:
    """`group.lock` locks every group: fired from `pc`, outside, naming `pa`'s window or not,
    `pa`'s group is locked either way."""
    _group_two(bench)
    seen = []
    for fields in ("action = 'enable'", "action = 'enable', window = 'class:pa'"):
        bench.focus("pc")
        bench.fire(f"hl.dsp.group.lock{{ {fields} }}")
        seen.append(_tries_to_join(bench))
        bench.fire("hl.dsp.group.lock{ action = 'disable' }")
    bench.close_all()
    return Verdict("group.lock", "window", seen[0] != seen[1], {"pc_joined": seen})


def _move_window_window(bench: Bench) -> Verdict:
    """`group.move_window` with `window = 'class:pc'` while `pb` is focused moves whom?"""
    default = _move_window_order(bench, "")
    named = _move_window_order(bench, "window = 'class:pc'")
    return Verdict(
        "group.move_window",
        "window",
        named != default,
        {"default": default, "window_pc": named},
    )


def _fullscreen_snapshot(bench: Bench) -> list[Any]:
    clients = bench.nested.hyprctl("clients") or []
    return sorted(
        [c["class"], c["fullscreen"], c["fullscreenClient"], c["at"], c["size"]]
        for c in clients
    )


def _layout_aware(bench: Bench) -> list[Verdict]:
    """`layout_aware` on both fullscreen dispatchers, at a grouped window and at a free one,
    in both modes, each against the same call without the key."""
    _group_two(bench)
    results: list[Verdict] = []
    calls = {
        "window.fullscreen": ("", "mode = 'maximized'"),
        "window.fullscreen_state": ("internal = 1, client = 1", "internal = 2, client = 0"),
    }
    for path, bases in calls.items():
        seen: dict[str, Any] = {}
        acted = False
        for target in ("pb", "pc"):
            for base in bases:
                snapshots = []
                for extra in ("", "layout_aware = true", "layout_aware = false"):
                    bench.focus(target)
                    fields = ", ".join(f for f in (base, extra) if f)
                    bench.fire(f"hl.dsp.{path}{{ {fields} }}")
                    snapshots.append(_fullscreen_snapshot(bench))
                    bench.fire("hl.dsp.window.fullscreen_state{ internal = 0, client = 0 }")
                acted = acted or snapshots[0] != snapshots[1] or snapshots[0] != snapshots[2]
                seen[f"{target}: {base or 'no fields'}"] = snapshots[0]
        results.append(Verdict(path, "layout_aware", acted, seen))
    bench.close_all()
    return results


def probe_unseen(nested: NestedHyprland) -> tuple[list[Verdict], str]:
    """The keys the group and fullscreen dispatchers take without any effect to read back.

    One verdict per key: `acted` is False while the compositor ignores it. They stay out of the
    record (it holds what acted); a catalog entry that gives one a row must first make this
    return `acted`, and `test_the_unseen_keys_still_do_nothing` says when that happened.
    """
    if shutil.which("foot") is None:
        return [], "foot is not installed, so no window could be opened to fire at"
    bench = Bench(nested)
    verdicts = [
        _gate_window(bench, "group.lock_active"),
        _gate_window(bench, "window.deny_from_group"),
        _group_lock_window(bench),
        _move_window_window(bench),
        *_layout_aware(bench),
    ]
    return verdicts, ""


def probe_effects(nested: NestedHyprland) -> tuple[list[EffectResult], str]:
    """The keys only a fired dispatcher shows. `("", reason)` when `foot` is missing."""
    if shutil.which("foot") is None:
        return [], "foot is not installed, so no window could be opened to fire at"
    bench = Bench(nested)
    results = [
        _toggle_family(bench, "window.float", "floating", float_first=False),
        _toggle_family(bench, "window.pin", "pinned", float_first=True),
        _pseudo_action(bench),
        _dpms_action(bench),
        _rename_name(bench),
        _toggle_special_positional(bench),
        *_cycle_next(bench),
        _group_gate_action(bench, "group.lock"),
        _group_gate_action(bench, "group.lock_active"),
        _group_gate_action(bench, "window.deny_from_group"),
        _move_window_forward(bench),
    ]
    return results, ""


# --- the whole probe -------------------------------------------------------------------


def probe_dispatchers(
    nested: NestedHyprland, plans: dict[str, Plan] | None = None, *, effects: bool = True
) -> ProbeRecord:
    """Probe each planned dispatcher on `nested`, which must be a `NestedHyprland`."""
    version = (nested.hyprctl("version") or {}).get("tag", "")
    record = ProbeRecord(hyprland_version=str(version).removeprefix("v"))
    for path, plan in (plans or PLANS).items():
        record.dispatchers[path] = probe_construction(nested, path, plan)
    if effects:
        record.effects, record.effects_skipped = probe_effects(nested)
        for result in record.effects:
            entry = record.dispatchers.get(result.path)
            if entry is not None and result.confirmed and result.key != "<positional>":
                entry.confirmed_keys = (*entry.confirmed_keys, result.key)
    return record


# --- the catalog against the record ----------------------------------------------------


def _takes_bare(data: DispatcherRecord) -> bool:
    """A bare string or number loads, and an empty table does not: the call is positional."""
    bare = data.form("bare_string").accepted or data.form("bare_number").accepted
    return bare and not data.form("empty_table").accepted


def _positional_confirmed(record: ProbeRecord, entry: Dispatcher) -> bool:
    """Positional by effect (a name that only works bare), or by the forms alone."""
    fired = any(
        e.path == entry.path and e.key == "<positional>" and e.confirmed for e in record.effects
    )
    return fired or _takes_bare(record.dispatchers[entry.path])


def check_entry(record: ProbeRecord, entry: Dispatcher) -> list[str]:
    """What the record says is wrong with one curated catalog entry; empty when it agrees.

    Pure: it reads the record, not a compositor, so the unit tier can run it on every
    commit against the committed record, and a release check can run it on a fresh one.
    A free-form entry makes no claim, so it has nothing to disagree with.
    """
    if entry.free_form_reason is not None or entry.path not in record.dispatchers:
        return []
    data = record.dispatchers[entry.path]
    problems: list[str] = []
    names = {spec.name for spec in entry.args}
    required = {spec.name for spec in entry.args if spec.required}

    if entry.positional:
        if not _positional_confirmed(record, entry):
            problems.append("is positional but the compositor takes no bare argument")
        if len(entry.args) != 1:
            problems.append("is positional with other than one argument")
        elif entry.args[0].required == data.form("no_args").accepted:
            problems.append(
                "marks its bare argument required or optional against the no-argument call"
            )
        return problems

    if not data.form("base_table").accepted:
        problems.append("the probe's own table call was refused; the plan is wrong")
    if _takes_bare(data):
        problems.append("takes a bare argument, but the entry is not positional")
    if required != set(data.required_keys):
        problems.append(
            f"required keys {sorted(required)} differ from the compositor's "
            f"{sorted(data.required_keys)}"
        )
    known = data.known_keys
    if lost := sorted(known - names):
        problems.append(f"omits keys the compositor reads: {lost}")
    if invented := sorted(names - known):
        problems.append(f"names keys no probe saw the compositor read: {invented}")
    return problems


def check_catalog(record: ProbeRecord, catalog: Iterable[Dispatcher] = CATALOG) -> list[str]:
    """`check_entry` over the whole catalog, one line per disagreement."""
    return [
        f"{entry.path}: {problem}"
        for entry in catalog
        for problem in check_entry(record, entry)
    ]


# --- the generated calls, asked of the compositor --------------------------------------


def sample_value(path: str, spec: ArgSpec) -> str:
    """A Lua literal the compositor accepts for this argument."""
    override = PATH_SAMPLES.get((path, spec.name))
    return override or SAMPLE_VALUES.get(spec.name) or SAMPLE_BY_TYPE[spec.type]


def generated_calls(entry: Dispatcher) -> Iterator[tuple[str, str]]:
    """`(label, Lua)` for the calls the bind editor would write for this entry.

    Required arguments alone, then every argument: the two shapes a user can save. Built the
    way `render_dispatcher` renders a `DispatcherCall`, from the entry's own specs.
    """
    call = f"hl.dsp.{entry.path}"
    required = [s for s in entry.args if s.required]
    for label, specs in (("required", required), ("all", list(entry.args))):
        if entry.positional:
            argument = ", ".join(sample_value(entry.path, s) for s in specs[:1])
            yield label, f"{call}({argument})"
        else:
            yield (
                label,
                f"{call}{_table(f'{s.name} = {sample_value(entry.path, s)}' for s in specs)}",
            )


def check_generated_calls(nested: NestedHyprland, entry: Dispatcher) -> list[str]:
    """Ask the compositor whether the calls the editor would write for `entry` load."""
    if entry.free_form_reason is not None:
        return []
    problems = []
    for label, lua in generated_calls(entry):
        accepted, error = _eval(nested, lua)
        if not accepted:
            problems.append(f"{label} call `{lua}` is refused: {error}")
    return problems


def verify_catalog(
    nested: NestedHyprland, record: ProbeRecord, catalog: Iterable[Dispatcher] = CATALOG
) -> list[str]:
    """The release check's one question: does the catalog still match this compositor?

    Both halves: the record against the entries (keys, required flags, positional), and the
    calls the editor would write against the compositor itself.
    """
    problems = check_catalog(record, catalog)
    for entry in catalog:
        problems.extend(f"{entry.path}: {p}" for p in check_generated_calls(nested, entry))
    return problems
