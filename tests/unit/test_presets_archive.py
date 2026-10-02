"""Theme archives: export a Preset, read one back, and refuse a hostile one (#169).

An archive is untrusted input from another machine. Every hostile shape here is built in the
test with `tarfile` into the test's own directory, read with the working directory moved
inside that directory too, and the whole directory is snapshot before and after: a member
that escaped (`../x`, an absolute name, a link) would show up as a new file in it.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import tarfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hyprtweaker.engine.presets import CaptureScope, Preset, PresetStore
from hyprtweaker.engine.presets_archive import (
    ArchiveNotWritten,
    ArchiveRefused,
    ArchiveWritten,
    BinaryZstd,
    Codec,
    CodecError,
    StdlibZstd,
    ThemeArchive,
    Wallpaper,
    export_archive,
    find_codec,
    read_archive,
)

MiB = 1024 * 1024


def png(width: int = 2, height: int = 1) -> bytes:
    """A real, decodable PNG: what an exported wallpaper is."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(b"\x00" + b"\x2e\x34\x40" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000") + bytes.fromhex(
    "ffc0001108" + "0002" + "0003" + "03012200021101031101" + "ffd9"
)
"""The head of a 3x2 JPEG: SOI, APP0, SOF0. Enough for the type and size to be read."""


def codecs() -> list[Codec]:
    """Every codec this machine has: the `zstd` binary always (CI and the PKGBUILD have
    it), and `compression.zstd` when the interpreter is 3.14 or newer."""
    found: list[Codec] = []
    stdlib = find_codec(which=lambda _name: None)
    if stdlib is not None:
        found.append(stdlib)
    executable = shutil.which("zstd")
    assert executable is not None, "the zstd binary is a build dependency (PKGBUILD, #182)"
    found.append(BinaryZstd(executable))
    return found


def nord(wallpaper: str | None = None) -> Preset:
    return Preset(
        name="Nord",
        created=datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
        scopes=frozenset({CaptureScope.GAPS_LAYOUT, CaptureScope.COLORS}),
        options={
            "general:border_size": 3,
            "general:gaps_in": "5 10 5 10",
            "general:col.active_border": "ee33ccff 45deg",
        },
        app_version="0.1.0",
        hyprland_version="0.56.2",
        wallpaper=wallpaper,
    )


def tree(root: Path) -> dict[str, bytes | None]:
    """Every path under `root`, with a file's bytes: what a write anywhere would change."""
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def member_names(path: Path, codec: Codec) -> list[str]:
    with (
        path.open("rb") as raw,
        codec.decompressed(raw) as stream,
        tarfile.open(fileobj=stream, mode="r|") as tar,  # type: ignore[call-overload]
    ):
        return [member.name for member in tar]


# --- export and read back ------------------------------------------------------------------


def test_an_archive_round_trips_a_preset_and_its_wallpaper_byte_for_byte(
    tmp_path: Path,
) -> None:
    image = tmp_path / "Pictures" / "fjord.png"
    image.parent.mkdir()
    image.write_bytes(png(4, 3))
    for codec in codecs():
        dest = tmp_path / f"nord-{type(codec).__name__}.hyprtweaker-theme"

        written = export_archive(nord(str(image)), dest, find=lambda c=codec: c)

        assert written == ArchiveWritten(dest, wallpaper_left_out=None)
        assert member_names(dest, codec) == ["preset.json", "wallpaper.png"]
        for reader in codecs():
            archive = read_archive(dest, find=lambda r=reader: r)
            assert archive == ThemeArchive(
                preset=nord(wallpaper=None),
                wallpaper=Wallpaper("png", png(4, 3)),
                newer_format=False,
                dropped=(),
            )


def test_a_preset_without_a_wallpaper_exports_its_settings_alone(tmp_path: Path) -> None:
    dest = tmp_path / "nord.hyprtweaker-theme"

    assert export_archive(nord(), dest) == ArchiveWritten(dest, wallpaper_left_out=None)

    assert member_names(dest, codecs()[0]) == ["preset.json"]
    archive = read_archive(dest)
    assert isinstance(archive, ThemeArchive)
    assert archive.preset == nord() and archive.wallpaper is None


def test_a_wallpaper_that_is_gone_is_left_out_and_said(tmp_path: Path) -> None:
    dest = tmp_path / "nord.hyprtweaker-theme"
    gone = tmp_path / "gone.png"

    written = export_archive(nord(str(gone)), dest)

    assert written == ArchiveWritten(
        dest, wallpaper_left_out="The wallpaper gone.png could not be read, so it was left out."
    )
    assert member_names(dest, codecs()[0]) == ["preset.json"]


def test_a_failed_export_leaves_the_file_that_was_there(tmp_path: Path) -> None:
    class Broken:
        def compress(self, data: bytes) -> bytes:
            raise CodecError("disk on fire")

        def decompressed(self, source: Any) -> Any:
            raise AssertionError("not read")

    dest = tmp_path / "nord.hyprtweaker-theme"
    dest.write_bytes(b"the archive from yesterday")
    before = tree(tmp_path)

    written = export_archive(nord(), dest, find=lambda: Broken())

    assert written == ArchiveNotWritten("The theme could not be compressed: disk on fire.")
    assert tree(tmp_path) == before


def test_without_zstd_export_and_import_say_to_install_it(tmp_path: Path) -> None:
    dest = tmp_path / "nord.hyprtweaker-theme"
    export_archive(nord(), dest)
    before = tree(tmp_path)
    install = "Theme archives need zstd. Install the zstd package, then try again."

    assert export_archive(nord(), tmp_path / "x.hyprtweaker-theme", find=lambda: None) == (
        ArchiveNotWritten(install)
    )
    assert read_archive(dest, find=lambda: None) == ArchiveRefused(install)
    assert tree(tmp_path) == before


def test_the_codec_is_the_standard_library_then_the_binary_then_none() -> None:
    def missing(name: str) -> Any:
        raise ModuleNotFoundError(name)

    class Zstd:
        ZstdError = Exception

    assert isinstance(
        find_codec(import_module=lambda _name: Zstd, which=lambda _n: None), StdlibZstd
    )
    binary = find_codec(import_module=missing, which=lambda name: f"/opt/bin/{name}")
    assert binary == BinaryZstd("/opt/bin/zstd")
    assert find_codec(import_module=missing, which=lambda _name: None) is None


# --- what a newer or a sloppier app wrote ----------------------------------------------------


def theme_bytes(
    members: list[tuple[tarfile.TarInfo, bytes | None]], *, trailing: bytes = b""
) -> bytes:
    """A tar.zst of exactly `members`, as a hostile or careless tool might write it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            if data is not None and info.isreg():
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return codecs()[-1].compress(buffer.getvalue() + trailing)


def entry(
    name: str, data: bytes | None = b"", kind: bytes = tarfile.REGTYPE
) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    return info, data


def preset_json(**changes: Any) -> bytes:
    data: dict[str, Any] = {
        "format": 1,
        "name": "Nord",
        "created": "2026-10-02T09:30:00+00:00",
        "scopes": ["gaps-layout"],
        "options": {"general:border_size": 3},
        "app_version": "0.1.0",
        "hyprland_version": "0.56.2",
        "wallpaper": None,
    }
    data.update(changes)
    return json.dumps(data).encode()


def test_a_newer_format_is_read_for_the_keys_this_build_knows(tmp_path: Path) -> None:
    path = tmp_path / "future.hyprtweaker-theme"
    path.write_bytes(
        theme_bytes([entry("preset.json", preset_json(format=2, sparkle={"level": 9}))])
    )

    archive = read_archive(path)

    assert isinstance(archive, ThemeArchive)
    assert archive.newer_format is True
    assert archive.preset.options == {"general:border_size": 3}


def test_a_value_no_option_could_hold_is_dropped_and_named(tmp_path: Path) -> None:
    path = tmp_path / "odd.hyprtweaker-theme"
    options = {"general:border_size": 3, "general:gaps_in": [5, 10], "misc:x": {"a": 1}}
    path.write_bytes(theme_bytes([entry("preset.json", preset_json(options=options))]))

    archive = read_archive(path)

    assert isinstance(archive, ThemeArchive)
    assert archive.preset.options == {"general:border_size": 3}
    assert archive.dropped == ("general:gaps_in", "misc:x")


def test_the_archives_own_wallpaper_path_is_never_kept(tmp_path: Path) -> None:
    path = tmp_path / "nord.hyprtweaker-theme"
    path.write_bytes(
        theme_bytes(
            [
                entry("preset.json", preset_json(wallpaper="/home/someone/.ssh/id_rsa")),
                entry("wallpaper.jpeg", JPEG),
            ]
        )
    )

    archive = read_archive(path)

    assert isinstance(archive, ThemeArchive)
    assert archive.preset.wallpaper is None
    assert archive.wallpaper == Wallpaper("jpeg", JPEG)


# --- hostile archives: refused with a sentence, nothing written anywhere --------------------

TOO_BIG = (
    "This theme archive holds more than 64 MiB, more than any theme needs, "
    "so it was not opened."
)
NOTHING = ", so nothing was imported."
DAMAGED = "This theme archive is damaged or incomplete, so nothing was imported."


def hostile_cases(tmp_path: Path) -> dict[str, tuple[bytes, str]]:
    good = entry("preset.json", preset_json())
    symlink = entry("wallpaper.png", None, tarfile.SYMTYPE)
    symlink[0].linkname = "/etc/passwd"
    hardlink = entry("wallpaper.png", None, tarfile.LNKTYPE)
    hardlink[0].linkname = "preset.json"
    escape = str(tmp_path / "escaped.json")
    oversized = tarfile.TarInfo("wallpaper.png")
    oversized.size = 65 * MiB
    claim = codecs()[-1].compress(
        preset_entry_bytes() + oversized.tobuf(tarfile.PAX_FORMAT) + b"\x89PNG"
    )
    whole = theme_bytes([good])

    def held(what: str) -> str:
        return f"This theme archive holds {what}, which a theme never contains{NOTHING}"

    return {
        "traversal": (
            theme_bytes([good, entry("../escaped.png", png())]),
            held("a file at “../escaped.png”"),
        ),
        "absolute": (
            theme_bytes([entry(escape, preset_json())]),
            held(f"a file at “{escape}”"),
        ),
        "nested": (
            theme_bytes([entry("sub/preset.json", preset_json())]),
            held("a file at “sub/preset.json”"),
        ),
        "other name": (
            theme_bytes([good, entry("run.sh", b"rm -rf ~")]),
            held("a file named “run.sh”"),
        ),
        "gif": (
            theme_bytes([good, entry("wallpaper.gif", b"GIF89a")]),
            held("a file named “wallpaper.gif”"),
        ),
        "symlink": (theme_bytes([good, symlink]), held("a link named “wallpaper.png”")),
        "hardlink": (theme_bytes([good, hardlink]), held("a link named “wallpaper.png”")),
        "directory": (
            theme_bytes([entry("wallpaper.png", None, tarfile.DIRTYPE), good]),
            held("a folder named “wallpaper.png”"),
        ),
        "device": (
            theme_bytes([good, entry("wallpaper.png", None, tarfile.CHRTYPE)]),
            held("a device or pipe named “wallpaper.png”"),
        ),
        "fifo": (
            theme_bytes([good, entry("wallpaper.png", None, tarfile.FIFOTYPE)]),
            held("a device or pipe named “wallpaper.png”"),
        ),
        "duplicate": (
            theme_bytes([good, good]),
            "This theme archive holds preset.json twice, so nothing was imported.",
        ),
        "two images": (
            theme_bytes([good, entry("wallpaper.png", png()), entry("wallpaper.jpg", JPEG)]),
            "This theme archive holds more than a preset and one wallpaper" + NOTHING,
        ),
        "image lies": (
            theme_bytes([good, entry("wallpaper.png", JPEG)]),
            "Its wallpaper is not the PNG image its name says, so nothing was imported.",
        ),
        "huge image": (
            theme_bytes([good, entry("wallpaper.png", png_header(20000, 20000))]),
            "Its wallpaper is 20000 by 20000 pixels, larger than this app opens" + NOTHING,
        ),
        "member claims over the cap": (claim, TOO_BIG),
        "stream over the cap": (theme_bytes([good], trailing=bytes(65 * MiB)), TOO_BIG),
        "preset.json over 1 MiB": (
            theme_bytes([entry("preset.json", preset_json(name="x" * MiB))]),
            "Its preset.json is larger than 1 MiB, more than any preset needs" + NOTHING,
        ),
        "truncated": (whole[: len(whole) // 2], DAMAGED),
        "not tar": (codecs()[-1].compress(b"just some words, not a tar " * 40), DAMAGED),
        "not zstd": (b"PK\x03\x04 a zip file pretending", "This file is not a theme archive."),
        "empty": (b"", "This file is not a theme archive."),
        "no preset.json": (
            theme_bytes([entry("wallpaper.png", png())]),
            "This theme archive has no preset.json, so there is nothing to import.",
        ),
        "not json": (
            theme_bytes([entry("preset.json", b"{nope")]),
            "Its preset.json is not valid JSON, so nothing was imported.",
        ),
        "nan": (
            theme_bytes([entry("preset.json", b'{"format": 1, "x": NaN}')]),
            "Its preset.json is not valid JSON, so nothing was imported.",
        ),
        "not an object": (
            theme_bytes([entry("preset.json", b"[1, 2]")]),
            "Its preset.json does not describe a preset, so nothing was imported.",
        ),
        "no format": (
            theme_bytes([entry("preset.json", preset_json(format=None))]),
            "Its preset.json does not describe a preset, so nothing was imported.",
        ),
        "no name": (
            theme_bytes([entry("preset.json", preset_json(name=""))]),
            "Its preset.json is missing the preset's name, date or settings" + NOTHING,
        ),
    }


def preset_entry_bytes() -> bytes:
    """`preset.json` as the first tar member, without the end-of-archive blocks."""
    info = tarfile.TarInfo("preset.json")
    data = preset_json()
    info.size = len(data)
    padding = (512 - len(data) % 512) % 512
    return info.tobuf(tarfile.PAX_FORMAT) + data + bytes(padding)


def png_header(width: int, height: int) -> bytes:
    return png()[:16] + struct.pack(">II", width, height) + png()[24:]


@pytest.mark.parametrize(
    "case",
    [
        "traversal", "absolute", "nested", "other name", "gif", "symlink", "hardlink",
        "directory", "device", "fifo", "duplicate", "two images", "image lies", "huge image",
        "member claims over the cap", "stream over the cap", "preset.json over 1 MiB",
        "truncated", "not tar", "not zstd", "empty", "no preset.json", "not json", "nan",
        "not an object", "no format", "no name",
    ],
)  # fmt: skip
def test_a_hostile_archive_is_refused_with_a_sentence_and_writes_nothing(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)  # where a relative member name would land, if anything wrote it
    data, sentence = hostile_cases(tmp_path)[case]
    path = tmp_path / "theme.hyprtweaker-theme"
    path.write_bytes(data)
    before = tree(tmp_path)

    for codec in codecs():
        assert read_archive(path, find=lambda c=codec: c) == ArchiveRefused(sentence)

    assert tree(tmp_path) == before


def test_reading_never_extracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Members are read by name with `extractfile`; the extractors, which resolve a member's
    name against a directory, are never reached."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an archive member was extracted to disk")

    monkeypatch.setattr(tarfile.TarFile, "extract", refuse)
    monkeypatch.setattr(tarfile.TarFile, "extractall", refuse)
    path = tmp_path / "nord.hyprtweaker-theme"
    path.write_bytes(
        theme_bytes([entry("preset.json", preset_json()), entry("wallpaper.png", png())])
    )

    assert isinstance(read_archive(path), ThemeArchive)


# --- into the store ---------------------------------------------------------------------------


def test_an_import_never_replaces_a_preset_or_an_image(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    store.write("nord", nord())
    stale = store.wallpaper_dir / "nord-2.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"an image from a preset deleted by hand")
    before = tree(tmp_path)

    slug, added = store.add(nord(), ("png", png()))

    assert (slug, added.name) == ("nord-3", "Nord 3")
    assert added.wallpaper == str(store.wallpaper_dir / "nord-3.png")
    assert (store.wallpaper_dir / "nord-3.png").read_bytes() == png()
    after = tree(tmp_path)
    assert {key: after[key] for key in before} == before
    assert set(after) - set(before) == {"presets/nord-3.json", "presets/wallpapers/nord-3.png"}


def test_deleting_an_imported_preset_deletes_its_image(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    slug, _ = store.add(nord(), ("png", png()))

    store.delete(slug)

    assert tree(tmp_path) == {"presets": None, "presets/wallpapers": None}
