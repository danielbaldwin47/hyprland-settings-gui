"""The Bridge registry: what the app knows about each v1 theming tool (ADR-0006).

Per-tool integration is data -- "template + config stanza + detection rule" -- so adding a
tool later is one more entry here. Every fact below comes from `docs/research/theming-tools.md`
(#167), which read each tool's source for Hyprland 0.56; the section names it cites are that
file's.

Two facts from that research shape every entry:

- **A tool's module has its own contract.** matugen's and wallust's app-owned templates set
  Options when required; noctalia 5's module returns a table whose `apply_theme()` does it;
  DMS's sets them on load. So an entry carries the exact Entrypoint text that loads it, not
  only a module path.
- **One spelling per module.** `require("a/b")` and `require("a.b")` load as two modules, so
  a self-applying module required both ways applies twice. Each entry stores the one spelling
  the Entrypoint uses; `same_module` is how anything *detecting* a require matches both.

Nothing here runs a tool or reads a file. Detection is the rule (`version_state`) over facts
a caller gathered from the tool path and the config home (#166).
"""

from __future__ import annotations

import enum
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template


class Mechanism(enum.StrEnum):
    """How a tool comes to emit Lua (ADR-0006 §Decision)."""

    ADOPT = "adopt"
    """The tool already ships Hyprland Lua: noctalia 5, DMS 1.5+."""

    TEMPLATE_PACK = "template-pack"
    """The app installs a template and a config stanza: matugen, wallust, shell-switch."""


class Contract(enum.StrEnum):
    """What requiring the module does (#167 Cross-cutting 2). Informational: the line an
    entry stores already accounts for it."""

    DATA = "data"
    """Returns a table and sets nothing (matugen's official template)."""

    APPLY = "apply"
    """Returns a table whose `apply_theme()` sets Options (noctalia 5)."""

    EFFECT = "effect"
    """Sets Options when loaded (DMS, every app-owned template)."""


class VersionState(enum.StrEnum):
    """Whether the installed version of a tool emits Lua this app can load (S5)."""

    SUPPORTED = "supported"
    NEEDS_UPDATE = "needs-update"


@dataclass(frozen=True, slots=True)
class BridgeModule:
    """One file a tool writes and the one Entrypoint line that loads it."""

    module: str
    """The `require` name, in the tool's one spelling. Quarantine's key for this file."""

    line: str
    """The exact Entrypoint text that loads it, e.g. `require("noctalia").apply_theme()`."""

    file: str
    """Where the tool writes it, relative to the hypr dir (`require` resolves only there)."""


@dataclass(frozen=True, slots=True)
class Detection:
    """How to tell a tool is here, from binary names and files only -- never by running it.

    File paths are relative to the config home (`$XDG_CONFIG_HOME`), so a test or a sandbox
    that roots `ConfigPaths` elsewhere never reads the owner's.
    """

    binaries: tuple[str, ...]
    """Names looked up on the tool path. Any one found means installed."""

    config_files: tuple[str, ...] = ()
    """The tool's own config, which names the Hyprland output it renders."""

    supported_by: str | None = None
    """A file only a supported version writes. `None`: being installed is enough."""

    outdated_by: tuple[str, ...] = ()
    """Files only an unsupported version writes."""

    needs_update: str = ""
    """What the user is told when the installed version cannot be bridged."""


@dataclass(frozen=True, slots=True)
class Choice:
    """A parameter picked from a list, passed as `flag value`."""

    key: str
    title: str
    flag: str
    choices: tuple[str, ...]
    default: str


@dataclass(frozen=True, slots=True)
class Number:
    """A numeric parameter, passed as `flag value`, or left off when unset."""

    key: str
    title: str
    flag: str
    minimum: float
    maximum: float
    default: float | None = None
    """`None` leaves the flag off, so the tool's own default applies."""


Parameter = Choice | Number


@dataclass(frozen=True, slots=True)
class TemplateFile:
    """A file the Template pack installs, relative to the config home."""

    path: str
    text: str


@dataclass(frozen=True, slots=True)
class TemplatePack:
    """The templates and the config stanza that make a tool write its Bridge module.

    The stanza is rendered with absolute paths (`stanza_for`), never `~`: a non-default
    `XDG_CONFIG_HOME` still has to work, and the confirm shows the paths in `~/` form.
    No stanza installs a reload hook: every v1 tool writes a `require`d file in place, and
    that write already reloads Hyprland (#167 Cross-cutting 1).
    """

    templates: tuple[TemplateFile, ...]
    config_file: str | None
    """The tool's config the stanza goes into, relative to the config home."""

    stanza: str = ""
    """TOML with `$template` and `$output` placeholders, each filled in as a TOML string."""

    patch: str = ""
    """Text the user applies to their own copy of the tool (shell-switch); the app never
    edits it."""

    def stanza_for(self, config_home: Path, hypr_dir: Path, output: str) -> str:
        """The stanza with the first template's and `output`'s absolute paths filled in,
        quoted so any path, one with a `'` in it included, reads back as itself."""
        template = config_home / self.templates[0].path if self.templates else config_home
        return Template(self.stanza).substitute(
            template=toml_string(str(template)), output=toml_string(str(hypr_dir / output))
        )


def toml_string(value: str) -> str:
    """`value` as a TOML string: a literal one where it can be, else an escaped basic one."""
    if "'" not in value and not any(ord(char) < 32 or ord(char) == 127 for char in value):
        return f"'{value}'"
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Everything the app knows about one theming tool."""

    tool: str
    """The id stored in the Manifest."""

    title: str
    """The name the user sees."""

    mechanism: Mechanism
    contract: Contract
    modules: tuple[BridgeModule, ...]

    owned_keys: tuple[str, ...]
    """The colon-form Options the module sets, read off its template statically: the app
    never evaluates tool Lua (ADR-0018). What "Set by <tool>" is built from."""

    color_source: bool
    """One of ADR-0014's Wallpaper backends: matugen or wallust."""

    detection: Detection
    parameters: tuple[Parameter, ...] = ()
    rerun: tuple[str, ...] = ()
    """Arguments that regenerate from an image, `{image}` standing for it. Empty: the app
    offers no Regenerate (noctalia and DMS hold their own wallpaper; shell-switch has none)."""

    template_pack: TemplatePack | None = None

    @property
    def sets_colors(self) -> bool:
        """Whether a Preset or Manual Color source gates this bridge off (ADR-0014)."""
        return any(is_color_key(key) for key in self.owned_keys)

    def rerun_argv(
        self, binary: Path, image: Path, values: Mapping[str, str | float | None] = {}
    ) -> tuple[str, ...]:
        """The argument list that regenerates from `image`. Never a shell string.

        `values` overrides each parameter's default by key; a `Number` with no value leaves
        its flag off.
        """
        if not self.rerun:
            raise ValueError(f"{self.title} has no regenerate command")
        argv = [str(binary), *(str(image) if arg == "{image}" else arg for arg in self.rerun)]
        for parameter in self.parameters:
            value = values.get(parameter.key, parameter.default)
            if value is not None:
                argv += [parameter.flag, _number(value)]
        return tuple(argv)


def _number(value: str | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def is_color_key(name: str) -> bool:
    """Whether an Option holds a colour or a gradient, by its name alone.

    Hyprland names every one `col.*` or `*color*`; the registry test holds this against the
    whole Schema, so a release that breaks the convention fails there.
    """
    leaf = name.rsplit(":", 1)[-1]
    return leaf.startswith("col.") or re.search(r"(^|_)color(_|$)", leaf) is not None


def same_module(a: str, b: str) -> bool:
    """Whether two `require` names reach the same file: `dms.colors` is `dms/colors`."""
    return a.replace(".", "/") == b.replace(".", "/")


def version_state(
    spec: ToolSpec, *, binaries: Collection[str], files: Collection[str]
) -> VersionState:
    """Whether the installed `spec` can be bridged, from what detection found.

    `binaries` are the names found on the tool path, `files` the config-home-relative paths
    that exist. A version is never asked of the tool: `--version` would run it (S3).
    """
    installed = any(name in binaries for name in spec.detection.binaries)
    supported = installed and (
        spec.detection.supported_by is None or spec.detection.supported_by in files
    )
    if not supported and any(path in files for path in spec.detection.outdated_by):
        return VersionState.NEEDS_UPDATE
    return VersionState.SUPPORTED


# --- templates (#167; none names a reload command) ------------------------------------------

MATUGEN_TEMPLATE = """\
-- Generated by matugen from {{image}}. Installed by hyprtweaker: do not edit.
local colors = {
    image = "{{image}}",
<* for name, value in colors *>
    {{name}} = "0xff{{value.default.hex_stripped}}",
<* endfor *>
}

hl.config({
    general = {
        col = {
            active_border = colors.primary,
            inactive_border = colors.outline_variant,
        },
    },
    group = {
        col = {
            border_active = colors.primary,
            border_inactive = colors.outline_variant,
            border_locked_active = colors.error,
            border_locked_inactive = colors.outline_variant,
        },
    },
})

return colors
"""
"""#167 § matugen, "What the app supplies": the official table, so `require(...).primary`
keeps working for variables, plus the effect that makes "set by matugen" true."""

WALLUST_TEMPLATE = """\
-- Generated by wallust ({{ backend }}/{{ palette }}). Installed by hyprtweaker: do not edit.
local colors = {
    background = "rgb({{ background | strip }})",
    foreground = "rgb({{ foreground | strip }})",
    cursor = "rgb({{ cursor | strip }})",
    color0 = "rgb({{ color0 | strip }})",
    color1 = "rgb({{ color1 | strip }})",
    color2 = "rgb({{ color2 | strip }})",
    color3 = "rgb({{ color3 | strip }})",
    color4 = "rgb({{ color4 | strip }})",
    color5 = "rgb({{ color5 | strip }})",
    color6 = "rgb({{ color6 | strip }})",
    color7 = "rgb({{ color7 | strip }})",
    color8 = "rgb({{ color8 | strip }})",
    color9 = "rgb({{ color9 | strip }})",
    color10 = "rgb({{ color10 | strip }})",
    color11 = "rgb({{ color11 | strip }})",
    color12 = "rgb({{ color12 | strip }})",
    color13 = "rgb({{ color13 | strip }})",
    color14 = "rgb({{ color14 | strip }})",
    color15 = "rgb({{ color15 | strip }})",
}

hl.config({
    general = {
        col = {
            active_border = {
                colors = {
                    "rgb({{ color1 | saturate(0.6) | strip }})",
                    "rgb({{ color2 | saturate(0.6) | strip }})",
                    "rgb({{ color3 | saturate(0.6) | strip }})",
                },
                angle = 45,
            },
            inactive_border = "rgba({{ color0 | strip }}ee)",
        },
    },
    group = {
        col = {
            border_active = "rgb({{ color1 | strip }})",
            border_inactive = "rgba({{ color0 | strip }}ee)",
        },
    },
})

return colors
"""
"""#167 § wallust, completed with the group pair its draft left as a comment. No literal
`{{`, `{%` or `{#` outside the template tags (#167 Cross-cutting 5)."""

SHELL_START_TEMPLATE = """\
-- managed by shell-switch
hl.on("hyprland.start", function()
    hl.exec_cmd([==[{{LAUNCH_CMD}}]==])
end)
"""

SHELL_BINDS_TEMPLATE = """\
-- managed by shell-switch
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd([==[{{LAUNCHER_CMD}}]==]))
"""

SHELL_SWITCH_PATCH = """\
# shell-switch: write Lua for Hyprland, which hyprtweaker loads.
# Three changes to your own copy of shell-switch; hyprtweaker never edits it.
#
# 1. get_startup_config_path: for hyprland, return
#      "$HOME/.config/hypr/shell-switcher-startup.lua"
# 2. get_binds_config_path: for hyprland, return
#      "$HOME/.config/hypr/shell-switcher-binds.lua"
# 3. update_compositor_configs: for hyprland, render
#      templates/hyprland/shell-start.lua.template and
#      templates/hyprland/shell-binds.lua.template
#    in place of the two .conf.template files.
"""

_HYPRTWEAKER_TEMPLATE = "hyprtweaker-hyprland.lua"
"""The installed template's file name: our own, so a user's `hyprland-colors.lua` (the name
matugen's README suggests) is never the file the app overwrites."""


MATUGEN = ToolSpec(
    tool="matugen",
    title="matugen",
    mechanism=Mechanism.TEMPLATE_PACK,
    contract=Contract.EFFECT,
    modules=(
        BridgeModule(
            module="hyprtweaker/bridge/matugen",
            line='require("hyprtweaker/bridge/matugen")',
            file="hyprtweaker/bridge/matugen.lua",
        ),
    ),
    owned_keys=(
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
        "group:col.border_locked_active",
        "group:col.border_locked_inactive",
    ),
    color_source=True,
    detection=Detection(binaries=("matugen",), config_files=("matugen/config.toml",)),
    parameters=(
        Choice("mode", "Mode", "-m", ("dark", "light"), "dark"),
        Choice(
            "scheme",
            "Scheme",
            "-t",
            (
                "scheme-tonal-spot",
                "scheme-content",
                "scheme-expressive",
                "scheme-fidelity",
                "scheme-fruit-salad",
                "scheme-monochrome",
                "scheme-neutral",
                "scheme-rainbow",
            ),
            "scheme-tonal-spot",
        ),
        Number("contrast", "Contrast", "--contrast", -1.0, 1.0),
    ),
    # `--source-color-index 0`: without it, or a TTY, a multi-colour image fails (#167).
    rerun=("image", "{image}", "--source-color-index", "0"),
    template_pack=TemplatePack(
        templates=(
            TemplateFile(f"matugen/templates/{_HYPRTWEAKER_TEMPLATE}", MATUGEN_TEMPLATE),
        ),
        config_file="matugen/config.toml",
        stanza=(
            "[templates.hyprtweaker_hyprland]\ninput_path = $template\noutput_path = $output\n"
        ),
    ),
)

WALLUST = ToolSpec(
    tool="wallust",
    title="wallust",
    mechanism=Mechanism.TEMPLATE_PACK,
    contract=Contract.EFFECT,
    modules=(
        BridgeModule(
            module="hyprtweaker/bridge/wallust",
            line='require("hyprtweaker/bridge/wallust")',
            file="hyprtweaker/bridge/wallust.lua",
        ),
    ),
    owned_keys=(
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
    ),
    color_source=True,
    detection=Detection(binaries=("wallust",), config_files=("wallust/wallust.toml",)),
    # Light or dark is `-S` on 4.x and a palette name on 3.x (#167), so no parameter is
    # offered that one of the two would reject.
    rerun=("run", "{image}"),
    template_pack=TemplatePack(
        templates=(
            TemplateFile(f"wallust/templates/{_HYPRTWEAKER_TEMPLATE}", WALLUST_TEMPLATE),
        ),
        config_file="wallust/wallust.toml",
        # `template` is relative to wallust's templates directory; `target` is absolute.
        stanza=(
            "[templates]\n"
            f"hyprtweaker_hyprland = {{ template = '{_HYPRTWEAKER_TEMPLATE}', "
            "target = $output }\n"
        ),
    ),
)

NOCTALIA = ToolSpec(
    tool="noctalia",
    title="noctalia",
    mechanism=Mechanism.ADOPT,
    contract=Contract.APPLY,
    # Its own spelling and native path. The Entrypoint carrying this line, commented or
    # not, is also what stops noctalia's `apply.sh` appending it after `user` (#167 C4).
    modules=(
        BridgeModule(
            module="noctalia",
            line='require("noctalia").apply_theme()',
            file="noctalia.lua",
        ),
    ),
    owned_keys=(
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
        "group:col.border_locked_active",
        "group:col.border_locked_inactive",
        "group:groupbar:col.active",
        "group:groupbar:col.inactive",
        "group:groupbar:col.locked_active",
        "group:groupbar:col.locked_inactive",
        "group:groupbar:text_color",
        "group:groupbar:text_color_inactive",
        "group:groupbar:text_color_locked_active",
        "group:groupbar:text_color_locked_inactive",
    ),
    color_source=False,
    detection=Detection(
        binaries=("noctalia",),
        config_files=("noctalia/hyprtweaker.toml",),
        outdated_by=("hypr/noctalia/noctalia-colors.lua", "hypr/noctalia/noctalia-colors.conf"),
        needs_update="noctalia 4 found. Update noctalia to 5 to set its colors up here.",
    ),
    template_pack=TemplatePack(
        templates=(),
        config_file="noctalia/hyprtweaker.toml",
        stanza='[theme.templates]\nbuiltin_ids = ["hyprland"]\n',
    ),
)

DMS = ToolSpec(
    tool="dms",
    title="DMS",
    mechanism=Mechanism.ADOPT,
    contract=Contract.EFFECT,
    # `colors.lua` only in v1; DMS's layout, cursor and bind files stay DMS's (S5).
    modules=(
        BridgeModule(module="dms.colors", line='require("dms.colors")', file="dms/colors.lua"),
    ),
    owned_keys=(
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
        "group:col.border_locked_active",
        "group:col.border_locked_inactive",
        "group:groupbar:col.active",
        "group:groupbar:col.inactive",
        "group:groupbar:col.locked_active",
        "group:groupbar:col.locked_inactive",
    ),
    color_source=False,
    detection=Detection(
        binaries=("dms",),
        supported_by="hypr/dms/colors.lua",
        outdated_by=("hypr/dms/hypr-colors.conf",),
        needs_update="DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer.",
    ),
)

SHELL_SWITCH = ToolSpec(
    tool="shell-switch",
    title="shell-switch",
    mechanism=Mechanism.TEMPLATE_PACK,
    contract=Contract.EFFECT,
    modules=(
        BridgeModule(
            module="shell-switcher-startup",
            line='require("shell-switcher-startup")',
            file="shell-switcher-startup.lua",
        ),
        BridgeModule(
            module="shell-switcher-binds",
            line='require("shell-switcher-binds")',
            file="shell-switcher-binds.lua",
        ),
    ),
    owned_keys=(),
    color_source=False,
    detection=Detection(
        binaries=("shell-switch",), config_files=("shell-switch/shell-switch",)
    ),
    template_pack=TemplatePack(
        templates=(
            TemplateFile(
                "shell-switch/templates/hyprland/shell-start.lua.template", SHELL_START_TEMPLATE
            ),
            TemplateFile(
                "shell-switch/templates/hyprland/shell-binds.lua.template", SHELL_BINDS_TEMPLATE
            ),
        ),
        config_file=None,
        patch=SHELL_SWITCH_PATCH,
    ),
)


REGISTRY: Mapping[str, ToolSpec] = {
    spec.tool: spec for spec in (NOCTALIA, DMS, SHELL_SWITCH, MATUGEN, WALLUST)
}
"""Every v1 tool, in the order the Entrypoint requires them: the shells first, then the two
Color sources, so the backend the user chose is the bridge that loads last."""


def spec_for_module(
    module: str, registry: Mapping[str, ToolSpec] = REGISTRY
) -> ToolSpec | None:
    """The tool whose module `module` names, in either spelling."""
    for spec in registry.values():
        if any(same_module(each.module, module) for each in spec.modules):
            return spec
    return None
