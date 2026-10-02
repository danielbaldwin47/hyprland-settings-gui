"""Theme archives: a Preset as one file to share, and reading one back (ADR-0014 §Sharing).

`<slug>.hyprtweaker-theme` is a zstd-compressed tar of exactly `preset.json` (the Preset
file's own JSON) and, when the Preset captured one, `wallpaper.<png|jpg|jpeg|webp>`.

An archive comes from another machine, so reading one is reading untrusted input, and the
rules are structural rather than checks bolted onto an extractor:

- **Nothing is extracted.** Members are read by name into memory with `extractfile()`;
  `extract`/`extractall`, which resolve a member's name against a directory, are never
  called. This module writes no file but the archive it exports; an import's writes are
  `PresetStore.add`'s, under a slug the store chooses.
- **Exactly two names are allowed**, `preset.json` and one `wallpaper.<ext>`, each a regular
  file. Every member is also put through the standard library's `tarfile.data_filter`
  (Python 3.11.4+, PEP 706), which refuses links out of the tree, device nodes and FIFOs; the
  name allow-list then refuses everything else it would have let through (a directory, any
  link, an absolute name it would have stripped, a path with a separator).
- **Bounds are counted while decompressing**, never trusted from a header: 64 MiB out of the
  decompressor in all, 1 MiB for `preset.json`, two members. Reading stops at the bound.
- **The image's type is its magic bytes**, which must agree with its extension, and its
  pixel size is read from its header and bounded, so the thumbnail decode cannot be told to
  allocate gigabytes.
- **Values are not parsed here.** `preset.json`'s values stay as stored, and enter the model
  only through `parse_value` against the running Schema (`Session.preview_preset`,
  `Session.apply_preset`). A value no Option could hold (a list, an object) is dropped and
  named.

Every refusal is a sentence (`ArchiveRefused.reason`), never an exception.

Compression: `compression.zstd` (Python 3.14+) when importable, else the `zstd` binary,
else export and import say to install zstd. The codec is a parameter, so all three are
tested on any interpreter.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import IO, Any, Final, Protocol

from .presets import (
    FORMAT,
    WALLPAPER_EXTENSIONS,
    Preset,
    preset_from_json,
    preset_to_json,
)

ARCHIVE_SUFFIX: Final = ".hyprtweaker-theme"
PRESET_MEMBER: Final = "preset.json"
WALLPAPER_STEM: Final = "wallpaper"

MAX_TOTAL: Final = 64 * 1024 * 1024
"""Bytes out of the decompressor, all members and padding together."""
MAX_PRESET_JSON: Final = 1024 * 1024
MAX_MEMBERS: Final = 2
MAX_WALLPAPER_PIXELS: Final = 1 << 26
"""67 megapixels: an 8K or a 16K-by-4K ultrawide wallpaper fits; its RGBA decode is 256 MiB."""

ZSTD_MAGIC: Final = b"\x28\xb5\x2f\xfd"

INSTALL_ZSTD: Final = "Theme archives need zstd. Install the zstd package, then try again."
NOT_AN_ARCHIVE: Final = "This file is not a theme archive."
DAMAGED: Final = "This theme archive is damaged or incomplete, so nothing was imported."
TOO_BIG: Final = (
    "This theme archive holds more than 64 MiB, more than any theme needs, so it was not "
    "opened."
)


def archive_name(slug: str) -> str:
    """The file name an export suggests for the Preset stored as `slug`."""
    return f"{slug}{ARCHIVE_SUFFIX}"


# --- results -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Wallpaper:
    """The image an archive carried: its type, from its magic bytes, and its bytes."""

    extension: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ThemeArchive:
    """An archive that read cleanly. Nothing has been written anywhere.

    `preset.wallpaper` is always `None`: the exporting machine's path means nothing here,
    and the image, if any, is `wallpaper`. `newer_format` is set when a newer app wrote it
    (its unknown keys were skipped); `dropped` names the settings whose value no Option
    could hold.
    """

    preset: Preset
    wallpaper: Wallpaper | None
    newer_format: bool
    dropped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchiveRefused:
    """The archive was not read, for `reason`: a sentence to show as it is."""

    reason: str


ArchiveRead = ThemeArchive | ArchiveRefused


@dataclass(frozen=True, slots=True)
class ArchiveWritten:
    """The archive is at `path`. `wallpaper_left_out` says why the image is missing from it,
    when the Preset named one that could not be read."""

    path: Path
    wallpaper_left_out: str | None


@dataclass(frozen=True, slots=True)
class ArchiveNotWritten:
    """Nothing was written, and whatever was at the destination is still there."""

    reason: str


ArchiveExport = ArchiveWritten | ArchiveNotWritten


# --- codecs --------------------------------------------------------------------------------


class CodecError(Exception):
    """The compressed bytes are not zstd, or end early, or the codec could not run."""


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class Codec(Protocol):
    def compress(self, data: bytes) -> bytes: ...

    def decompressed(self, source: IO[bytes]) -> AbstractContextManager[Readable]:
        """A stream of `source`'s decompressed bytes; its `read` raises `CodecError` on
        bytes that are not zstd or that end before the stream does."""
        ...


@dataclass(frozen=True, slots=True)
class StdlibZstd:
    """`compression.zstd`, Python 3.14+. Taken as a module so it is found, not imported:
    the floor is 3.11, where the import would fail type checking and at runtime."""

    module: Any

    def compress(self, data: bytes) -> bytes:
        compressed: bytes = self.module.compress(data)
        return compressed

    @contextmanager
    def decompressed(self, source: IO[bytes]) -> Iterator[Readable]:
        errors = (self.module.ZstdError, EOFError)
        try:
            stream = self.module.ZstdFile(source, "rb")
        except errors as error:
            raise CodecError(str(error)) from error
        with stream:
            yield _Translated(stream, errors)


@dataclass(frozen=True, slots=True)
class BinaryZstd:
    """The `zstd` binary, over pipes: never a shell, never a path of the user's argv-split."""

    executable: str

    def compress(self, data: bytes) -> bytes:
        try:
            done = subprocess.run(
                [self.executable, "-q", "-c"],
                input=data,
                capture_output=True,
                env=_tool_environment(),
                check=False,
            )
        except OSError as error:
            raise CodecError(error.strerror or str(error)) from error
        if done.returncode != 0:
            raise CodecError(done.stderr.decode(errors="replace").strip() or "zstd failed")
        return done.stdout

    @contextmanager
    def decompressed(self, source: IO[bytes]) -> Iterator[Readable]:
        try:
            process = subprocess.Popen(
                [self.executable, "-q", "-d", "-c"],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_tool_environment(),
            )
        except OSError as error:
            raise CodecError(error.strerror or str(error)) from error
        try:
            yield _ProcessOutput(process)
        finally:
            # Still running when a bound stopped the read: it is told to stop, not drained.
            process.kill()
            assert process.stdout is not None
            process.stdout.close()
            process.wait()


def find_codec(
    *,
    import_module: Callable[[str], ModuleType | Any] = importlib.import_module,
    which: Callable[[str], str | None] = shutil.which,
) -> Codec | None:
    """`compression.zstd` when this interpreter has it, else the `zstd` binary on `PATH`.

    `zstd` is not a theming tool, so it is looked up on the ordinary `PATH`.
    """
    try:
        return StdlibZstd(import_module("compression.zstd"))
    except ImportError:
        pass
    executable = which("zstd")
    return BinaryZstd(executable) if executable is not None else None


def _tool_environment() -> dict[str, str]:
    """The app's environment without the session's compositor and displays, as every spawn
    in the Engine is given (`migration/flow.py`)."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY")
    }


class _Translated:
    """A stream whose own errors are `CodecError`, so the reader needs to know no codec."""

    def __init__(self, stream: Readable, errors: tuple[type[BaseException], ...]) -> None:
        self._stream = stream
        self._errors = errors

    def read(self, size: int = -1, /) -> bytes:
        try:
            return self._stream.read(size)
        except self._errors as error:
            raise CodecError(str(error)) from error


class _ProcessOutput:
    """The binary's output; its end is checked against the exit status, so a truncated
    input reads as `CodecError` rather than as a short, clean stream."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        self._process = process
        self._stdout = process.stdout

    def read(self, size: int = -1, /) -> bytes:
        data: bytes = self._stdout.read(size)
        if not data and size != 0 and self._process.wait() != 0:
            raise CodecError("zstd could not decompress it")
        return data


class _TooLarge(Exception):
    pass


class _Bounded:
    """Counts the decompressed bytes as they come out, and stops at `limit`."""

    def __init__(self, stream: Readable, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._count = 0

    def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            size = self._limit - self._count + 1
        data = self._stream.read(size)
        self._count += len(data)
        if self._count > self._limit:
            raise _TooLarge
        return data

    def drain(self) -> None:
        """Read to the end: a stream cut short is found here, not trusted as complete."""
        while self.read(1 << 20):
            pass


# --- export --------------------------------------------------------------------------------


def export_archive(
    preset: Preset, dest: Path, *, find: Callable[[], Codec | None] = find_codec
) -> ArchiveExport:
    """Write `preset` as a Theme archive at `dest`, its wallpaper included when it has one.

    Written to a temporary name beside `dest` and renamed over it, so a failed export
    leaves whatever was at `dest` as it was.
    """
    codec = find()
    if codec is None:
        return ArchiveNotWritten(INSTALL_ZSTD)
    image, left_out = _exported_wallpaper(preset.wallpaper)
    data = dict(preset_to_json(preset))
    data["wallpaper"] = None if image is None else _wallpaper_member(image.extension)
    payload = json.dumps(data, indent=2).encode() + b"\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        _add(tar, PRESET_MEMBER, payload, preset.created.timestamp())
        if image is not None:
            _add(
                tar, _wallpaper_member(image.extension), image.data, preset.created.timestamp()
            )
    try:
        compressed = codec.compress(buffer.getvalue())
    except CodecError as error:
        return ArchiveNotWritten(f"The theme could not be compressed: {error}.")
    scratch = dest.with_name(f".{dest.name}.tmp")
    try:
        scratch.write_bytes(compressed)
        scratch.replace(dest)
    except OSError as error:
        scratch.unlink(missing_ok=True)
        return ArchiveNotWritten(f"The theme could not be saved: {error.strerror or error}.")
    return ArchiveWritten(dest, left_out)


def _add(tar: tarfile.TarFile, name: str, data: bytes, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), 0o644, int(mtime)
    tar.addfile(info, io.BytesIO(data))


def _exported_wallpaper(path: str | None) -> tuple[Wallpaper | None, str | None]:
    if path is None:
        return None, None
    name = Path(path).name
    try:
        with open(path, "rb") as file:
            data = file.read(MAX_TOTAL - MAX_PRESET_JSON + 1)
    except OSError:
        return None, f"The wallpaper {name} could not be read, so it was left out."
    if len(data) > MAX_TOTAL - MAX_PRESET_JSON:
        return (
            None,
            f"The wallpaper {name} is larger than a theme can carry, so it was left out.",
        )
    extension = _sniffed_type(data)
    if extension is None:
        return (
            None,
            f"The wallpaper {name} is not a PNG, JPEG or WebP image, so it was left out.",
        )
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in WALLPAPER_EXTENSIONS and _sniffed_type(data, suffix) == suffix:
        extension = suffix  # keep the user's `jpg` or `jpeg`
    return Wallpaper(extension, data), None


def _wallpaper_member(extension: str) -> str:
    return f"{WALLPAPER_STEM}.{extension}"


# --- import --------------------------------------------------------------------------------


def read_archive(path: Path, *, find: Callable[[], Codec | None] = find_codec) -> ArchiveRead:
    """Read the Theme archive at `path` into memory: its Preset and its image bytes.

    Writes nothing, anywhere. Every way an archive can be wrong is an `ArchiveRefused`.
    """
    codec = find()
    if codec is None:
        return ArchiveRefused(INSTALL_ZSTD)
    try:
        # Unbuffered, so the seek moves the descriptor the `zstd` binary reads from too: a
        # buffered seek back into its own buffer would leave the descriptor at the end.
        with path.open("rb", buffering=0) as raw:
            if raw.read(len(ZSTD_MAGIC)) != ZSTD_MAGIC:
                return ArchiveRefused(NOT_AN_ARCHIVE)
            raw.seek(0)
            with codec.decompressed(raw) as stream:
                bounded = _Bounded(stream, MAX_TOTAL)
                members = _read_members(bounded)
                if isinstance(members, ArchiveRefused):
                    return members
                bounded.drain()
    except OSError as error:
        return ArchiveRefused(f"The file could not be opened: {error.strerror or error}.")
    except _TooLarge:
        return ArchiveRefused(TOO_BIG)
    except (CodecError, tarfile.TarError):
        return ArchiveRefused(DAMAGED)
    return _theme(members)


def _read_members(stream: _Bounded) -> dict[str, bytes] | ArchiveRefused:
    """Each allowed member's bytes by name, read in archive order off a forward-only stream."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=stream, mode="r|") as tar:  # type: ignore[call-overload]
        for member in tar:
            refused = _refuse_member(member, found)
            if refused is not None:
                return refused
            limit = MAX_PRESET_JSON if member.name == PRESET_MEMBER else MAX_TOTAL
            if member.size > limit:
                return ArchiveRefused(TOO_BIG if limit == MAX_TOTAL else _PRESET_TOO_BIG)
            file = tar.extractfile(member)
            assert file is not None  # a regular file, checked above
            data = file.read(limit + 1)
            if len(data) > limit:
                return ArchiveRefused(TOO_BIG if limit == MAX_TOTAL else _PRESET_TOO_BIG)
            found[member.name] = data
    return found


_PRESET_TOO_BIG = (
    "Its preset.json is larger than 1 MiB, more than any preset needs, so nothing was imported."
)


def _refuse_member(member: tarfile.TarInfo, found: dict[str, bytes]) -> ArchiveRefused | None:
    if len(found) >= MAX_MEMBERS:
        return ArchiveRefused(
            "This theme archive holds more than a preset and one wallpaper, so nothing was "
            "imported."
        )
    try:
        tarfile.data_filter(member, _NOWHERE)
    except tarfile.FilterError:
        return _held(member)
    if not member.isreg() or member.name not in _ALLOWED:
        return _held(member)
    if member.name in found:
        return ArchiveRefused(
            f"This theme archive holds {member.name} twice, so nothing was imported."
        )
    if member.name != PRESET_MEMBER and any(name != PRESET_MEMBER for name in found):
        return ArchiveRefused(
            "This theme archive holds more than a preset and one wallpaper, so nothing was "
            "imported."
        )
    return None


_NOWHERE = "/hyprtweaker-theme-archive"
"""The destination `data_filter` judges against. Nothing is ever extracted there or anywhere:
the filter is asked only whether extracting this member would be safe."""

_ALLOWED = frozenset({PRESET_MEMBER, *(_wallpaper_member(ext) for ext in WALLPAPER_EXTENSIONS)})


def _held(member: tarfile.TarInfo) -> ArchiveRefused:
    name = _shown(member.name)
    if member.issym() or member.islnk():
        what = f"a link named “{name}”"
    elif member.isdir():
        what = f"a folder named “{name}”"
    elif not member.isreg():
        what = f"a device or pipe named “{name}”"
    elif "/" in member.name or "\\" in member.name or member.name in ("..", "."):
        what = f"a file at “{name}”"
    else:
        what = f"a file named “{name}”"
    return ArchiveRefused(
        f"This theme archive holds {what}, which a theme never contains, so nothing was "
        "imported."
    )


def _shown(name: str) -> str:
    """A member name as a sentence can quote it: printable, and not a page long."""
    printable = "".join(char if char.isprintable() else "?" for char in name)
    return printable if len(printable) <= 160 else printable[:159] + "…"


def _theme(members: dict[str, bytes]) -> ArchiveRead:
    raw = members.get(PRESET_MEMBER)
    if raw is None:
        return ArchiveRefused(
            "This theme archive has no preset.json, so there is nothing to import."
        )
    image: Wallpaper | None = None
    for name, data in members.items():
        if name != PRESET_MEMBER:
            image_or_refusal = _wallpaper(name.rpartition(".")[2], data)
            if isinstance(image_or_refusal, ArchiveRefused):
                return image_or_refusal
            image = image_or_refusal
    parsed = _preset(raw)
    if isinstance(parsed, ArchiveRefused):
        return parsed
    preset, newer, dropped = parsed
    return ThemeArchive(preset, image, newer, dropped)


def _preset(raw: bytes) -> tuple[Preset, bool, tuple[str, ...]] | ArchiveRefused:
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_not_json)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return ArchiveRefused("Its preset.json is not valid JSON, so nothing was imported.")
    fmt = data.get("format") if isinstance(data, dict) else None
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1:
        return ArchiveRefused(
            "Its preset.json does not describe a preset, so nothing was imported."
        )
    options = data.get("options")
    dropped: tuple[str, ...] = ()
    if isinstance(options, dict):
        dropped = tuple(key for key, value in options.items() if not _scalar(value))
        data["options"] = {key: value for key, value in options.items() if _scalar(value)}
    try:
        preset = preset_from_json(data)
    except (LookupError, ValueError, TypeError, AttributeError):
        preset = None
    if preset is None:
        return ArchiveRefused(
            "Its preset.json is missing the preset's name, date or settings, so nothing was "
            "imported."
        )
    return replace(preset, wallpaper=None), fmt > FORMAT, dropped


def _not_json(constant: str) -> Any:
    raise ValueError(f"{constant} is not JSON")


def _scalar(value: Any) -> bool:
    """What a stored value can be (`stored_value`): a list or an object no Option holds."""
    return value is None or isinstance(value, bool | int | float | str)


# --- images --------------------------------------------------------------------------------

_TYPE_NAMES = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WebP"}


def _wallpaper(extension: str, data: bytes) -> Wallpaper | ArchiveRefused:
    if _sniffed_type(data, extension) != extension:
        return ArchiveRefused(
            f"Its wallpaper is not the {_TYPE_NAMES[extension]} image its name says, so "
            "nothing was imported."
        )
    size = _pixel_size(extension, data)
    if size is None:
        return ArchiveRefused(
            f"Its wallpaper is not a readable {_TYPE_NAMES[extension]} image, so nothing was "
            "imported."
        )
    width, height = size
    if width * height > MAX_WALLPAPER_PIXELS:
        return ArchiveRefused(
            f"Its wallpaper is {width} by {height} pixels, larger than this app opens, so "
            "nothing was imported."
        )
    return Wallpaper(extension, data)


def _sniffed_type(data: bytes, extension: str | None = None) -> str | None:
    """The image type `data` starts with, spelled as `extension` when that is its type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return extension if extension in ("jpg", "jpeg") else "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _pixel_size(extension: str, data: bytes) -> tuple[int, int] | None:
    """Width and height from the image's header, without decoding it."""
    if extension == "png":
        if data[12:16] != b"IHDR" or len(data) < 24:
            return None
        return int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
    if extension == "webp":
        return _webp_size(data)
    return _jpeg_size(data)


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(
            data[27:30], "little"
        ) + 1
    return None


_JPEG_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
"""The start-of-frame markers, which carry the size; C4, C8 and CC are other segments."""


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in _JPEG_FRAMES:
            if index + 9 > len(data):
                return None
            return int.from_bytes(data[index + 7 : index + 9]), int.from_bytes(
                data[index + 5 : index + 7]
            )
        index += 2 + int.from_bytes(data[index + 2 : index + 4])
    return None
