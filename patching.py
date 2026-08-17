"""The web face of pySAGE's `sage_patch`: what the patcher page offers, and how a POST from it
becomes a patched binary.

`sage_patch` describes its patches for a command line - one argparse parser per patch, parameters
as flags - so this module reads that description back out and turns it into something a form can
render and validate. A patch added to `sage_patch.registry` therefore appears on the page with its
options, its warning and its credit line without anything here being edited.

Most patches modify `game.dat`, but a few modify the launcher shim or Worldbuilder instead, so the
page is organised by :class:`Target` - the binary a patch expects - and asks for each file only
once something that needs it is picked.

No HTML lives here: the template asks a :class:`Target` and its :class:`PatchSpec`s what to draw.
"""

from __future__ import annotations

import argparse
import logging
import re
import secrets
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING, Any

from werkzeug.utils import secure_filename

try:
    from sage_ini.engine import dump_engine
    from sage_patch.patcher import EXPERIMENTAL_WARNING, apply_patches
    from sage_patch.registry import PATCHES
    from sage_patch.sagepatch import generate
# pysage-tools is optional - the page says so rather than the app failing to import.
except ImportError:
    PATCHES = {}
    EXPERIMENTAL_WARNING = ""
    apply_patches = None

if TYPE_CHECKING:
    from sage_patch.patcher import Patch
    from werkzeug.datastructures import FileStorage

log = logging.getLogger(__name__)

AVAILABLE = bool(PATCHES)

#: The engine, and the file this page is mostly about: it sorts first and is asked for up front,
#: where the other binaries are asked for only when a patch needs them.
GAME_DAT = "game.dat"

#: Where patched binaries wait to be downloaded, and for how long.
OUTPUT_ROOT = Path(gettempdir()) / "edain-patcher"
OUTPUT_TTL = 1800

#: The most a public endpoint will read, across every file in one submission - Flask checks this
#: against the whole request body, not against each file, so it has to cover a submission that
#: picks patches for every target at once. A ROTWK `game.dat` is ~11 MB and the launcher is small,
#: but `Worldbuilder.exe` is ~24 MB, so the three together already sit near 40 MB; the rest is
#: headroom for a binary somebody has already grown by other means.
MAX_UPLOAD_BYTES = 128 * 1024 * 1024

#: The name a `.sagepatch` has to have where it is going: `sage_ini` and `sage_lint` look for it
#: beside the mod's `.sagelint`, so the download is named for its destination.
SAGEPATCH_NAME = ".sagepatch"

#: The comment block at the top of a generated `.sagepatch`, mirroring what `sage-patch sagepatch`
#: writes, so a file that came from here reads like one that came from the CLI - because it is one.
SAGEPATCH_HEADER = (
    "Written by the Edain engine patcher - the INI surface this game.dat accepts.",
    "Commit it beside .sagelint; sage_lint and sage_ini read it (see sage_ini.engine).",
    "Regenerate it whenever the binary is repatched: `sage-patch sagepatch <game.dat>`.",
)

_TOKEN = re.compile(r"\A[A-Za-z0-9_-]{16,64}\Z")


class PatchError(Exception):
    """Something the person on the page can act on, phrased for them rather than for a log."""


def _target_of(cls: type[Patch]) -> tuple[str, str]:
    """The binary `cls` expects, and its description with any redundant binary prefix removed.

    `sage_patch` has no single attribute for the binary: the two launcher patches declare their own
    `binary`, and `desert-weather-wb` only says so at the front of its description
    ("Worldbuilder.exe (not game.dat): ..."). Both are read here, because a patch offered for the
    wrong file can only fail - and once the page groups patches under the binary they need, that
    prefix is saying twice what the heading already says.
    """
    declared = getattr(cls, "binary", "")
    head, marker, rest = cls.description.partition(" (not game.dat): ")
    if marker:
        return declared or head, rest

    return declared or GAME_DAT, cls.description


@dataclass(frozen=True)
class PatchParam:
    """One parameter of a patch, as a form needs it: a field kind, a default to pre-fill with and
    the flag and help text `sage-patch` documents it by."""

    dest: str
    flag: str
    kind: str  # "bool" | "int" | "choice" | "text"
    default: Any
    choices: tuple[str, ...]
    help: str


@dataclass(frozen=True)
class PatchSpec:
    """A patch as the page presents it, and the form field names it is read back through."""

    name: str
    binary: str
    description: str
    author: str
    experimental: bool
    params: tuple[PatchParam, ...]

    @property
    def credited(self) -> str:
        """Who to name for this patch, matching what `Patch.credit` says when nobody is recorded."""
        return self.author or "author unrecorded"

    @property
    def field(self) -> str:
        return f"patch:{self.name}"

    def param_field(self, param: PatchParam) -> str:
        return f"param:{self.name}:{param.dest}"


@dataclass(frozen=True)
class Target:
    """One binary the page can patch, and the patches that want it.

    The slug is what appears in field names and download URLs, so neither ever carries a filename:
    `Worldbuilder.exe` is `worldbuilder-exe` everywhere a name has to survive a URL.
    """

    name: str
    specs: tuple[PatchSpec, ...]

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")

    @property
    def field(self) -> str:
        return f"file:{self.slug}"

    @property
    def engine(self) -> bool:
        return self.name == GAME_DAT


def _parser(cls: type[Patch]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    cls.add_cli_arguments(parser)
    return parser


def _kind(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        return "bool"
    if action.choices:
        return "choice"
    if action.type is int:
        return "int"

    return "text"


def _params(cls: type[Patch]) -> tuple[PatchParam, ...]:
    # `_actions` is argparse's own list of registered arguments; it exposes no public reader for
    # them, and this is the only way to ask a patch what parameters it takes without duplicating
    # the answer here for every patch in the registry.
    return tuple(
        PatchParam(
            dest=action.dest,
            flag=action.option_strings[0] if action.option_strings else action.dest,
            kind=_kind(action),
            default=action.default,
            choices=tuple(str(choice) for choice in action.choices or ()),
            help=(action.help or "") % {"default": action.default},
        )
        for action in _parser(cls)._actions
    )


def _spec(name: str, cls: type[Patch]) -> PatchSpec:
    binary, description = _target_of(cls)
    return PatchSpec(
        name=name,
        binary=binary,
        description=description,
        author=cls.author,
        experimental=cls.experimental,
        params=_params(cls),
    )


def _targets() -> tuple[Target, ...]:
    specs = [_spec(name, cls) for name, cls in PATCHES.items()]
    binaries = {spec.binary for spec in specs}

    return tuple(
        Target(
            name=binary,
            # Settled patches first, alphabetically within each half: the page hides the
            # experimental ones behind a toggle, so they are a block rather than sprinkled through
            # the list. The bundled patches are order-independent (see sage_patch.patcher), so this
            # is presentation only.
            specs=tuple(
                sorted(
                    (spec for spec in specs if spec.binary == binary),
                    key=lambda spec: (spec.experimental, spec.name),
                )
            ),
        )
        # The engine first, then the rest alphabetically: it carries all but three of the patches
        # and is the file people come here with.
        for binary in sorted(
            binaries, key=lambda name: (name != GAME_DAT, name.lower())
        )
    )


#: Every binary this page can patch, engine first, each with the patches that want it.
TARGETS = _targets()

_BY_SLUG = {target.slug: target for target in TARGETS}


def _value(spec: PatchSpec, param: PatchParam, form: Mapping[str, str]) -> Any:
    field = spec.param_field(param)
    if param.kind == "bool":
        return field in form

    raw = (form.get(field) or "").strip()
    if not raw:
        # An empty box means "as `sage-patch` ships it", which for some parameters is None.
        return param.default

    if param.kind == "int":
        try:
            return int(raw)
        except ValueError:
            raise PatchError(
                f"{spec.name} {param.flag}: {raw!r} is not a whole number"
            ) from None

    if param.kind == "choice" and raw not in param.choices:
        raise PatchError(
            f"{spec.name} {param.flag}: {raw!r} is not one of {', '.join(param.choices)}"
        )

    return raw


def _patch(spec: PatchSpec, form: Mapping[str, str]) -> Patch:
    """Build `spec`'s patch the way the CLI builds it - a Namespace of its own defaults, overridden
    by what was submitted, handed to `from_cli_args` - so a patch that validates its arguments in
    `__init__` (`commandset-limit` refuses a count over 127) rejects a bad form field with the same
    message it would give on the command line."""
    cls = PATCHES[spec.name]
    args = _parser(cls).parse_args([])
    for param in spec.params:
        setattr(args, param.dest, _value(spec, param, form))

    try:
        return cls.from_cli_args(args)
    except ValueError as exc:
        raise PatchError(f"{spec.name}: {exc}") from exc


@dataclass(frozen=True)
class Selection:
    """The patches a submission picked for one binary: what was ticked, and the built instances."""

    target: Target
    specs: tuple[PatchSpec, ...]
    patches: tuple[Patch, ...]

    @property
    def names(self) -> str:
        return ", ".join(spec.name for spec in self.specs)


def selections(form: Mapping[str, str]) -> list[Selection]:
    """What `form` picked, grouped by the binary each patch needs, in :data:`TARGETS` order."""
    chosen = []
    for target in TARGETS:
        picked = tuple(spec for spec in target.specs if spec.field in form)
        if picked:
            chosen.append(
                Selection(
                    target=target,
                    specs=picked,
                    patches=tuple(_patch(spec, form) for spec in picked),
                )
            )

    return chosen


@dataclass(frozen=True)
class PatchedFile:
    """One binary that came out of a run, and what went into it."""

    binary: str
    slug: str
    filename: str
    credits: tuple[str, ...]
    experimental: tuple[str, ...]
    original_size: int
    patched_size: int
    #: Whether a `.sagepatch` was written beside this file, and anything the reader should know
    #: about what it does and does not describe.
    sagepatch: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchResult:
    """A finished run: the files to download, and who they owe credit to."""

    token: str
    files: tuple[PatchedFile, ...]

    @property
    def credits(self) -> tuple[str, ...]:
        """The credit line of every patch in the run, deduplicated across its files."""
        return tuple(sorted({credit for f in self.files for credit in f.credits}))

    @property
    def experimental(self) -> tuple[str, ...]:
        return tuple(sorted({name for f in self.files for name in f.experimental}))


def sweep() -> None:
    """Delete the workspaces of runs older than :data:`OUTPUT_TTL`.

    Called on every visit to the page rather than from a scheduler: uploads only accumulate while
    somebody is using this, so the traffic that creates the files is the traffic that clears them.
    """
    if not OUTPUT_ROOT.is_dir():
        return

    cutoff = time.time() - OUTPUT_TTL
    for workspace in OUTPUT_ROOT.iterdir():
        try:
            expired = workspace.stat().st_mtime < cutoff
        except OSError:
            continue

        if expired:
            shutil.rmtree(workspace, ignore_errors=True)


def _upload_for(selection: Selection, files: Mapping[str, FileStorage]) -> FileStorage:
    upload = files.get(selection.target.field)
    if upload is None or not upload.filename:
        verb = "patches" if len(selection.specs) == 1 else "patch"
        raise PatchError(
            f"{selection.names} {verb} {selection.target.name}, so upload that file as well."
        )

    return upload


def _write_sagepatch(
    selection: Selection, output: Path, workspace: Path
) -> tuple[bool, tuple[str, ...]]:
    """Write the `.sagepatch` describing what the patched engine now accepts, beside it.

    Generated from the finished binary rather than from the list of patches just applied, which is
    what `sage-patch sagepatch` does and the reason to do it that way: the mod can commit this file
    and have `sage-patch sagepatch --check` regenerate the same one from the same binary later. A
    file assembled from what this run happens to know would drift from that check immediately.

    The trade is that a patch whose `detect` cannot recover its parameters is not recognised in the
    binary and so goes undescribed. The CLI has the same blind spot and cannot say so; here the
    applied patches are known, so the difference is reported as a note instead of going quiet.

    Never raises: the patched binary is the deliverable and this is a sidecar, so a failure to
    describe it is a note on the page rather than a lost run.
    """
    try:
        # The name only, never the server's path: this string is written into a file somebody
        # commits to their mod.
        generated = generate(output.read_bytes(), Path(output.name))
        text = dump_engine(generated.engine, "\n".join(SAGEPATCH_HEADER))
    except Exception as exc:
        log.info("could not describe %s: %s", output.name, exc)
        return False, (
            f"No .sagepatch could be generated for this file ({type(exc).__name__}: {exc}).",
        )

    path = workspace / "sagepatch" / selection.target.slug / SAGEPATCH_NAME
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    recognised = {patch.name for patch in generated.patches}
    missing = [
        patch.name for patch in selection.patches if patch.name not in recognised
    ]
    notes = list(generated.notes)
    if missing:
        notes.append(
            "applied but not recognised when reading the binary back, so what they add to the INI "
            f"is not described here: {', '.join(sorted(missing))}. This is what "
            "`sage-patch sagepatch` would produce too - add those fields by hand if your mod "
            "lints against them."
        )

    return True, tuple(notes)


def _patch_one(
    selection: Selection, upload: FileStorage, workspace: Path
) -> PatchedFile:
    target = selection.target
    filename = secure_filename(upload.filename) or target.name
    output = workspace / "out" / target.slug / filename
    output.parent.mkdir(parents=True)

    source = workspace / f"upload-{target.slug}.bin"
    upload.save(source)

    with source.open("rb") as f:
        if f.read(2) != b"MZ":
            raise PatchError(
                f"{filename} is not a Windows executable. Upload {target.name} itself, not an "
                "archive or an installer containing it."
            )

    try:
        apply_patches(source, list(selection.patches), output=output)
    except Exception as exc:
        # Every patch verifies the bytes it is about to change, so the usual failure here is a
        # binary that is not the build the patch was written against (or one that already carries
        # the patch), and the patch's own message says which site disagreed.
        log.info("patching %s failed: %s", filename, exc)
        raise PatchError(
            f"{filename}: {type(exc).__name__}: {exc}. Nothing was written."
        ) from exc

    original_size = source.stat().st_size
    source.unlink()

    # Only for the engine: a `.sagepatch` describes the INI a `game.dat` accepts, which is not a
    # question the launcher or Worldbuilder answer.
    sagepatch, notes = (
        _write_sagepatch(selection, output, workspace) if target.engine else (False, ())
    )

    return PatchedFile(
        binary=target.name,
        slug=target.slug,
        filename=filename,
        credits=tuple(patch.credit for patch in selection.patches),
        experimental=tuple(
            str(patch) for patch in selection.patches if patch.experimental
        ),
        original_size=original_size,
        patched_size=output.stat().st_size,
        sagepatch=sagepatch,
        notes=notes,
    )


def apply_selected(
    chosen: list[Selection], files: Mapping[str, FileStorage]
) -> PatchResult:
    """Patch each binary `chosen` names and keep the results for :func:`output_for` to serve.

    All or nothing across binaries: if the launcher patch fails, the game.dat that patched cleanly
    is discarded with it, because a half-delivered set is the thing somebody ships by accident.
    Every upload is deleted as soon as it has been read; only the patched copies are kept, and only
    until the sweep takes them.
    """
    if not chosen:
        raise PatchError("Pick at least one patch to apply.")

    # Ask for the missing file before writing anything, so a forgotten upload costs a message
    # rather than a patch run.
    uploads = [_upload_for(selection, files) for selection in chosen]

    token = secrets.token_urlsafe(16)
    workspace = OUTPUT_ROOT / token
    try:
        patched = tuple(
            _patch_one(selection, upload, workspace)
            for selection, upload in zip(chosen, uploads)
        )
    except PatchError:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    return PatchResult(token=token, files=patched)


def download_name(output: Path, slug: str, sagepatch: bool) -> str:
    """What to call `output` on the way out, which for a `.sagepatch` is not what it is called
    where it is going.

    `.sagepatch` is all extension and no basename, and that is not a name a browser will save: a
    leading dot is stripped so that a page cannot quietly write a hidden file, so the file arrives
    as `sagepatch` however the header asks for it. Prefixing the binary it describes gives a name
    that survives verbatim - `game.dat.sagepatch` - and moves the correction to a rename the result
    page asks for, rather than one the reader has to work out from a name they did not choose.
    """
    if not sagepatch:
        return output.name

    return f"{_BY_SLUG[slug].name}{SAGEPATCH_NAME}"


def output_for(token: str, slug: str, sagepatch: bool = False) -> Path | None:
    """A download link's file - the patched binary, or the `.sagepatch` beside it - or None if it
    has expired.

    `token` and `slug` are both checked against what this module issues, and the directory they
    name holds exactly one file - so the name the browser saves under never comes from the URL, and
    there is nothing here for a crafted path to reach.
    """
    if not _TOKEN.fullmatch(token) or slug not in _BY_SLUG:
        return None

    # iterdir rather than glob: the sagepatch is a dotfile, and glob's treatment of those is a
    # detail this does not need to depend on.
    directory = OUTPUT_ROOT / token / ("sagepatch" if sagepatch else "out") / slug
    outputs = list(directory.iterdir()) if directory.is_dir() else []
    if len(outputs) != 1 or not outputs[0].is_file():
        return None

    return outputs[0]
