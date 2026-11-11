<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/wheelgap/wheelgap/main/docs/logo/wheelgap-lockup-dark.png">
    <img alt="wheelgap" src="https://raw.githubusercontent.com/wheelgap/wheelgap/main/docs/logo/wheelgap-lockup.png" height="72">
  </picture>
</h1>

[![CI](https://github.com/wheelgap/wheelgap/actions/workflows/ci.yml/badge.svg)](https://github.com/wheelgap/wheelgap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/wheelgap)](https://pypi.org/project/wheelgap/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/wheelgap/wheelgap/blob/main/LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![good first issues](https://img.shields.io/github/issues/wheelgap/wheelgap/good%20first%20issue?label=good%20first%20issues&color=7057ff)](https://github.com/wheelgap/wheelgap/labels/good%20first%20issue)

**Find the Python dependencies that have no prebuilt wheel for your target platform, before your Docker build does.**

Point it at your lockfile and the platforms you ship to (ARM servers, Alpine images, Windows on ARM, free-threaded Python 3.14t). It tells you which packages pip or uv will have to compile from source, and which can't be installed at all.

📊 **New: [wheel coverage for the top 5,000 PyPI packages](https://wheelgap.github.io/tracker/)**, by platform and Python version, updated nightly.

![wheelgap demo](https://raw.githubusercontent.com/wheelgap/wheelgap/main/docs/demo.gif)

## Install and run (under 30 seconds)

With [uv](https://docs.astral.sh/uv/):

```bash
uvx wheelgap
```

Or with pipx:

```bash
pipx run wheelgap
```

Or install it: `pip install wheelgap`.

Run either one in a project directory. It finds `uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements.lock` or `requirements.txt` on its own.

## Usage

See the [recipes](https://github.com/wheelgap/wheelgap/blob/main/docs/recipes.md) for multi-arch images, monorepos with several lockfiles, and a free-threading readiness report you can use in scripts.

```bash
# Will my Alpine ARM image compile anything?
wheelgap -p 3.13 -t alpine-arm64

# Is my stack ready for free-threaded Python 3.14?
wheelgap -p 3.14t -t all

# Several Pythons at once, Linux on both architectures
wheelgap uv.lock -p 3.12,3.13,3.14 -t linux-x86_64,linux-aarch64

# No -p? A .python-version file next to uv.lock (written by uv or pyenv) sets the Python
wheelgap uv.lock -t linux-x86_64

# Check a Pipenv project's default and development dependencies
wheelgap Pipfile.lock -p 3.13 -t linux-x86_64,linux-aarch64

# Check a PDM project's locked distribution files
wheelgap pdm.lock -p 3.13 -t linux-x86_64,linux-aarch64

# No lockfile? Check packages directly (latest version, or pin one)
wheelgap psycopg2 uvloop numpy==2.3.4 -t windows,win-arm64

# Take the target from the final stage's image (e.g. python:3.13-alpine
# or ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim), following
# named stages like `FROM base AS runtime` back to the image they start from.
# A python X.Y tag without a Debian release (python:3.13-slim) is read as
# the release Docker Hub currently builds it on (trixie, glibc 2.41, for 3.9
# and later); patch tags like 3.12.7-slim keep the --glibc default. Pin
# -bookworm or -trixie for an answer that doesn't move
wheelgap --dockerfile Dockerfile

# Machine-readable, for scripts and CI
wheelgap --json > wheelgap.json

# Why is it a gap? List each rejected wheel and the reason
wheelgap -t alpine-arm64 --explain
```

Projects usually ship to the same platforms, so you can write the defaults down once in `pyproject.toml` (next to the lockfile, or in the current directory). Command-line flags still win.

```toml
[tool.wheelgap]
python = ["3.13", "3.14t"]
targets = ["linux-x86_64", "linux-aarch64", "musl-aarch64"]
glibc = "2.31"
```

For uv projects, wheelgap also reads `[tool.uv] required-environments` as default targets:

```toml
[tool.uv]
required-environments = ["sys_platform == 'linux' and platform_machine == 'aarch64'"]
```

Target selection uses `-t` first, then `--dockerfile`, then `[tool.wheelgap] targets`, then uv's
`required-environments`, then the built-in defaults. Each marker is evaluated against the eight common
native platforms using the selected Python versions. A platform is included if any marker matches
for any selected Python, and the resulting platforms are checked for all selected Pythons.
Linux markers select both glibc and musl targets, since markers cannot distinguish the libc;
the example above selects `linux-aarch64` and `musl-aarch64`. Use `-t` to choose one explicitly.
An inferred selection is explained on stderr, including with `--json` or `--markdown`.
The inferred targets replace the built-in defaults, so a project that only requires Intel macOS
wheels is then checked for Intel macOS only: add `[tool.wheelgap] targets` or `-t` to check more.
An absent or empty setting keeps the built-in defaults, and so does one matching none of the
targets above (with a note). An invalid marker is an error. Only `required-environments` is used here: `environments`, extra
Linux architectures, mobile and Pyodide targets are not inferred.

Each cell in the table is one of:

| cell | meaning |
| --- | --- |
| `✓` | a compatible wheel exists, so the install is fast |
| `● source` | no wheel, only an sdist, so it compiles at install time (needs a compiler, headers, time) |
| `✗ missing` | no wheel and no sdist, so the install fails |
| `–` | not installed on this target (environment markers exclude it) |
| `✓ bundled` | no wheel on PyPI, but Pyodide ships its own build (with `--pyodide-lock`) |

Packages that are fine everywhere are hidden; `--all` shows them.

With several targets, the summary also counts the gaps on each target that has any (`musl-aarch64  1 from source, 1 missing`). `--markdown` lists the same counts. `--json` has them for every target, with zeros where there are no gaps and also when only one target is checked, as `summary.per_target`, keyed by target label: `{"linux-aarch64": {"source": 0, "missing": 0, "unknown": 0}, "musl-aarch64": {"source": 1, "missing": 1, "unknown": 0}}`.

`--explain` lists, for each gap, the wheels that were rejected and why, closest misses first:

```
armlib 1.4.0 on musl-aarch64: ● source
  armlib-1.4.0-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl  manylinux_2_17_aarch64 is a glibc wheel; musl (Alpine) needs musllinux
```

For `uv.lock` inputs, `--explain` also shows why the package is installed at all: the shortest dependency path from your project (or from a dependency group, shown as `(dev group)`) to the gap package. With extra/group selection it's a path the selection installs it through, not always the shortest. Paths are worked out per target, since markers can pull a package in on one platform only. With `--json --explain`, each package gets a matching `via` object keyed by target, like `rejected`: `"via": {"win-arm64": ["myapp", "uvicorn", "uvloop"]}`. Other inputs have no dependency graph, so they show no path (and `"via": {}`).

The shape of `--json` output is described by [`docs/wheelgap-output.schema.json`](https://github.com/wheelgap/wheelgap/blob/main/docs/wheelgap-output.schema.json). It matches the wheelgap version it ships with, and later versions may add keys. In `--json` output, `results`, `rejected` and `via` are all keyed by target label: just the platform (`"linux-x86_64"`) when one Python is checked, or Python and platform (`"3.13 linux-x86_64"`) with several. The top-level `targets` list gives each target's `label`, `python` and `platform`, so a script can look the key up instead of building it:

```python
import json

data = json.load(open("wheelgap.json"))
key = next(t["label"] for t in data["targets"] if t["python"] == "3.13" and t["platform"] == "linux-x86_64")
```

```
uvloop 0.21.0 on win-arm64: ● source
  via: myapp → uvicorn → uvloop
```

If you think wheelgap got a package wrong, that output (or `--json --explain`) is all a bug report needs, with no need to share your lockfile.

**Exit codes:** `0` no gaps, `1` gaps found, `2` bad input. `--exit-zero` exits `0` even when gaps are found; bad input still exits `2`. `--fail-on missing` (or `fail-on = "missing"` in `[tool.wheelgap]`) accepts source builds and only exits `1` when a package can't be installed at all.

If an input can't be read (a missing lockfile, an unknown target, a bad `[tool.wheelgap]` value), `--json` prints `{"error": {"message": "...", "type": "TargetError"}}` on stdout instead of results, next to the usual `wheelgap: error:` line on stderr and exit code 2. Command-line usage errors from argparse are still plain text on stderr.

Use `--ignore psycopg2` for accepted gaps (repeatable or comma-separated, with case-insensitive package-name matching and equivalent hyphens, underscores and dots): matched packages are excluded from the table, counts and exit status, and listed as ignored in text (including quiet mode), JSON and Markdown. Set `ignore = ["psycopg2"]` in `[tool.wheelgap]` to use it by default; any `--ignore` flags replace the configured list rather than adding to it, and `--ignore ""` clears it for one run.

To accept a gap on one target only, add the target after a colon: `--ignore psycopg2:musl-x86_64` (or `ignore = ["psycopg2:musl-x86_64"]`) still checks psycopg2 everywhere else. The target takes anything `-t` does (`alpine`, `linux/amd64`, `all`), one per entry (`--ignore psycopg2:alpine,psycopg2:win-arm64`). An unknown target is an error (exit 2), and so is a second target after a comma (`psycopg2:alpine,win-arm64`), which would otherwise read as a package name. The package keeps its real status in the report (`● source (ignored)` in the table), but a gap on that target doesn't count towards the summary or the exit status, isn't explained by `--explain`, and is a skipped testcase in `--junit`. Reports list it as `ignored: psycopg2 (musl-x86_64)`, and `--json` puts it in `ignored_targets` (`ignored` keeps only packages ignored everywhere). A target that isn't checked in this run gets a note on stderr.

Use `-q` / `--quiet` (or `quiet = true` in `[tool.wheelgap]`) to show only the gap summary and package names. Clean runs without ignored packages are silent, JSON and Markdown output are unchanged, and quiet text output cannot be combined with `--explain` (`--json --explain` and `--markdown --explain` work).

### Check the uv dependencies you actually install

With no selection options, wheelgap keeps its conservative behavior: all project/workspace extras
and dependency groups count. For `uv.lock` inputs, select a deployment's dependencies explicitly:

```bash
# Match a production uv sync --no-dev --extra api
wheelgap uv.lock --no-dev --extra api -t linux-aarch64
# Include every project extra, or add a dependency group
wheelgap uv.lock --all-extras
wheelgap uv.lock --group docs
# Check only a group, without project dependencies or default groups
wheelgap uv.lock --only-group docs
```

`--extra`, `--group` and `--only-group` are repeatable. Once any selector is used, the project's runtime
dependencies and uv's default groups (`dev` unless `[tool.uv].default-groups` changes it) count,
unless `--only-group` is given;
project extras are included only when requested. `--no-dev` removes `dev`, while `--only-group`
excludes project dependencies and default groups. Combine `--only-group` with `--group` to add more
groups, but not with `--extra`/`--all-extras`. An explicit `dev` group conflicts with `--no-dev`.
Unknown extra/group names are errors. Command-line selectors need a `uv.lock` input, or for groups a Poetry 2.x
`poetry.lock`, or for `--extra`/`--all-extras` a `pyproject.toml` (see below); configured `groups` apply to
`uv.lock` and Poetry 2.x `poetry.lock` inputs, and configured `extras` only to `uv.lock` inputs.

`poetry.lock` (Poetry 2.x) inputs accept `--group`, `--only-group` and `--no-dev` with Poetry's meanings, and
configured `groups` apply to them too. `main` and every group not marked `optional = true` in
`pyproject.toml` count; `--group` adds a group (like `poetry install --with`), `--no-dev` leaves out `dev`,
and `--only-group` checks only the named groups (`main` only if named). A package counts if any of its groups is
selected, with the markers of those groups. `--extra` isn't supported for `poetry.lock` yet, and a command-line
selection on a Poetry 1.x lock (no per-package groups) is an error; configured `groups` leave a 1.x lock unfiltered.

Default selections can also be configured:

```toml
[tool.wheelgap]
extras = ["api"]
groups = ["docs"]
```

CLI `--extra` and `--group` replace their corresponding configured lists. `--only-group` suppresses
both configured lists, and `--no-dev` removes a configured `dev` group. Empty lists explicitly select
no extras or additional groups. With selection enabled, rooted workspaces start at the root project
(members outside the selection aren't checked, and a note on stderr names them);
virtual workspaces start at their members. Dependency-requested extras are followed transitively,
including local workspace dependencies, rather than enabling every extra of every dependency.

Unresolved marker conditions (including uv's synthetic conflict markers and unknown OS releases)
remain conservative: wheelgap can still report a possible gap rather than silently hide one.
Selection itself needs no network. Index refreshes (`--online`, or a target outside the lockfile's
`requires-python`) only look up packages in the selected graph. Configured `extras` only apply to `uv.lock` inputs, and configured `groups` to `uv.lock` and Poetry 2.x
`poetry.lock` inputs; other inputs are checked as usual.

### Check a library's direct dependencies

Libraries usually don't commit a lockfile. Pass the library's `pyproject.toml` to check the
dependencies it declares in `[project] dependencies`:

```bash
wheelgap pyproject.toml -t win-arm64 -t pyodide
# Add [project.optional-dependencies] groups
wheelgap pyproject.toml --extra test
wheelgap pyproject.toml --all-extras
```

Each requirement is read like a line of a requirements file, so ranges and bare names use the newest
matching release and markers are respected. With several `-p` versions, that release isn't filtered by
`Requires-Python` ([#84](https://github.com/wheelgap/wheelgap/issues/84)), so ranges and bare names can
show false gaps. Only direct dependencies are checked: their own dependencies aren't, and wheelgap always
prints a note saying so. Use a lockfile for the full picture. If `[project] requires-python` leaves out a `-p`
Python (every clause counts, as pip applies them), that target is still checked and a note names it; an invalid
`requires-python` is ignored with a note.
`[dependency-groups]` isn't read yet, so `--group`, `--only-group` and `--no-dev` are errors here, and a
`dependencies` listed in `[project] dynamic` is an input error (exit 2), as are dynamic
`optional-dependencies` with `--extra`/`--all-extras` and Poetry dependencies kept in
`[tool.poetry.dependencies]` (check `poetry.lock` instead). `wheelgap .` in a directory with no lockfile
doesn't switch to `pyproject.toml` on its own; the error suggests it. `check_lockfile()` and
`wheelgap diff` don't read `pyproject.toml`.

### Targets

Run `wheelgap --list-targets` to see target aliases and compatibility limits, or add `--json` for a machine-readable list.

If you know the deployment distro rather than its libc version, use `--distro`:

```bash
wheelgap uv.lock -p 3.13 -t linux-aarch64 --distro ubuntu-22.04
wheelgap uv.lock -p 3.13 -t musl-aarch64 --distro alpine
```

It supplies a glibc or musl version only: it does **not** select Python versions or platforms. Keep using `-t` for those platforms, including `musl-*` for Alpine. You can also set `distro = "ubuntu-22.04"` in `[tool.wheelgap]`.

| distro names | libc version | package sources |
| --- | --- | --- |
| `ubuntu-20.04`, `ubuntu-22.04`, `ubuntu-24.04` | glibc 2.31, 2.35, 2.39 | [Focal](https://launchpad.net/ubuntu/focal/+source/glibc), [Jammy](https://packages.ubuntu.com/jammy/libc6), [Noble](https://packages.ubuntu.com/noble/libc6) |
| `debian-11` / `bullseye`, `debian-12` / `bookworm`, `debian-13` / `trixie` | glibc 2.31, 2.36, 2.41 | [Bullseye](https://packages.debian.org/bullseye/libc6), [Bookworm](https://packages.debian.org/bookworm/libc6), [Trixie](https://packages.debian.org/trixie/libc6) |
| `rhel-8` / `rocky-8` / `alma-8`, `rhel-9` / `rocky-9` / `alma-9` | glibc 2.28, 2.34 | [RHEL 8](https://access.redhat.com/errata/RHSA-2021:1585), [RHEL 9](https://access.redhat.com/errata/RHBA-2022:3917) |
| `amazonlinux-2`, `amazonlinux-2023` | glibc 2.26, 2.34 | [Amazon Linux toolchain packages](https://docs.aws.amazon.com/linux/al2023/ug/glibc-gcc-and-binutils.html) |
| `alpine` | musl 1.2 (Alpine 3.13 and later) | [Alpine musl](https://pkgs.alpinelinux.org/package/v3.22/main/x86_64/musl) |

For each libc, an explicit `--glibc` / `--musl` wins, then `--distro`, then a version from `--dockerfile`, then a `glibc` / `musl` config value, then the config `distro`, then the built-in default. A distro only supplies its own libc; the other libc keeps its existing default. Names are case-insensitive; unknown names are errors. For older Alpine, pass `--musl 1.1` explicitly. The text `--list-targets` output also lists distro names; its JSON output remains a list of platforms.

Only x86 Linux takes `manylinux` wheels older than glibc 2.17: every other architecture's first manylinux was `manylinux2014` (glibc 2.17), so pip accepts nothing older there, and neither does wheelgap.

| name | aliases | wheel tags it accepts |
| --- | --- | --- |
| `linux-x86_64` | `linux`, `linux-amd64`, `linux/amd64` | `manylinux_2_*_x86_64` up to `--glibc` (default 2.36), legacy `manylinux2014`/`2010`/`1` |
| `linux-aarch64` | `linux-arm64`, `linux/arm64`, `linux/arm64/v8` | `manylinux_2_*_aarch64` from 2.17 up to `--glibc`, legacy `manylinux2014` |
| `musl-x86_64` | `alpine`, `musl` | `musllinux_1_*_x86_64` up to `--musl` (default 1.2) |
| `musl-aarch64` | `alpine-arm64` | `musllinux_1_*_aarch64` |
| `macos-arm64` | `macos`, `mac` | `macosx_*_arm64` and `universal2` up to `--macos` (default 14.0) |
| `macos-x86_64` | `macos-intel` | `macosx_*_x86_64` and `universal2` |
| `win-amd64` | `windows`, `win` | `win_amd64` |
| `win-arm64` | `windows-arm64` | `win_arm64` |
| `linux-armv7l` | `linux-arm`, `linux-armv7`, `linux/arm/v7` | `manylinux_2_*_armv7l` from 2.17 (32-bit ARM, e.g. Raspberry Pi OS), legacy `manylinux2014`, and plain `linux_armv7l` (which PyPI allows for the Pi) |
| `linux-ppc64le`, `linux-s390x`, `linux-riscv64` | `linux/ppc64le`, `linux/s390x`, `linux/riscv64` | `manylinux_2_*` from 2.17 for that arch (`riscv64` has no legacy `manylinux2014` tag) |
| `linux-i686` | `linux-x86`, `linux-386`, `linux/386` | `manylinux_2_*_i686` up to `--glibc`, legacy `manylinux2014`/`2010`/`1` (32-bit x86) |
| `win32` | `windows-x86`, `win-x86` | `win32` (32-bit Python on Windows) |
| `musl-armv7l`, `musl-ppc64le`, `musl-s390x`, `musl-riscv64`, `musl-i686` | `alpine-armv7`, `alpine-x86` | `musllinux_1_*` for that arch (pip would also take a plain `linux_armv7l` wheel on Alpine, but it's a glibc build, so wheelgap doesn't count it) |
| `pyodide` | `wasm`, `wasm32`, `emscripten` | Pyodide's ABI for that Python: `pyodide_2024_0` (3.12), `pyodide_2025_0` / `pyemscripten_2025_0` (3.13), `pyemscripten_2026_0` (3.14) |
| `ios-arm64` | `ios`, `iphoneos` | `ios_*_arm64_iphoneos` up to `--ios` (default 13.0) |
| `ios-simulator-arm64`, `ios-simulator-x86_64` | `ios-simulator` | `ios_*_arm64_iphonesimulator` / `ios_*_x86_64_iphonesimulator` |
| `android-arm64`, `android-x86_64` | `android` | `android_*_arm64_v8a` / `android_*_x86_64` up to `--android-api` (default 24) |

**iOS and Android** (PEP 730 and PEP 738, Python 3.13+) are checked with `-t mobile` or by name, for apps built with tools like BeeWare's Briefcase. Mobile dependencies are installed by a cross-platform pip run that can only use wheels, so a package with only an sdist shows as `✗ missing`. `--ios` and `--android-api` (or `ios = "15.0"` and `android-api = 28` in `[tool.wheelgap]`) set your app's minimum iOS version and Android API level. wheelgap checks PyPI only: Briefcase and Chaquopy also install from their own package repositories (BeeWare's on anaconda.org, and chaquo.com), which have mobile builds of packages like numpy, so a `✗ missing` there may still install.

**Pyodide (Python in the browser)** only installs wheels: there's no compiler, so a package with only an sdist shows as `✗ missing`, not `● source`. Pyodide wheels are tied to one ABI per Python version and don't work across them. Pyodide also ships its own builds of many popular packages (numpy, pydantic-core and others) that aren't on PyPI. Point `--pyodide-lock` at the release's `pyodide-lock.json` (a path or `https://` URL, also `pyodide-lock = "..."` in `[tool.wheelgap]`) and those count as `✓ bundled`, with a note when Pyodide's version differs from the one you pinned (shown with `-q` too, and `"bundled_version_differs": true` in `--json`). That still passes: `pyodide.loadPackage` gives you Pyodide's version, though `micropip.install` of your exact pin would fail.

```bash
wheelgap -p 3.12 -t pyodide --pyodide-lock https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide-lock.json
```

Docker platform names also work with `-t`, for example `-t linux/amd64,linux/arm64` (also `linux/arm64/v8`, `linux/arm/v7`, `linux/ppc64le`, `linux/s390x`, `linux/riscv64` and `linux/386`, also spelled `linux/i386`).

`-t all` checks the eight common native platforms, and `-t everything` adds the armv7l, ppc64le, s390x, riscv64 and i686 Linux targets (glibc and musl) and `win32`. Pyodide, iOS and Android are only checked when you name them. Python versions take a trailing `t` for free-threaded builds (`3.14t`), which correctly rejects `abi3` wheels. Without `-p`, wheelgap reads a `.python-version` file next to the lockfile (or `pyproject.toml`) if there is one, and otherwise uses the Python it is running on.

### In CI (GitHub Action)

```yaml
- uses: actions/checkout@v7
- uses: wheelgap/wheelgap@v0.8.0
  with:
    args: uv.lock -p 3.13 -t linux-x86_64,linux-aarch64
```

The job fails when a dependency would build from source on a target you ship, for example when a dependency bump drops ARM wheels. The gap table also goes to the run's summary page (set `summary: false` to turn that off).

Outside the Action, `--markdown` prints the same report as Markdown, for a job summary (`wheelgap --markdown >> "$GITHUB_STEP_SUMMARY"`) or a PR comment. With `--explain`, the rejected wheels go in a collapsed section. `--junit PATH` also writes a JUnit XML file (one testsuite per target) that GitLab, Jenkins, Azure Pipelines and CircleCI can show as a test report, alongside whatever output mode you use.

### With pre-commit

```yaml
repos:
  - repo: https://github.com/wheelgap/wheelgap
    rev: v0.8.0
    hooks:
      - id: wheelgap
```

The hook runs when `uv.lock`, `poetry.lock`, `pdm.lock`, `pylock.toml`, `Pipfile.lock`, or a `requirements*.txt` or `requirements*.lock` file changes. Put your targets in `[tool.wheelgap]` in `pyproject.toml` so the hook checks the right platforms. Pass extra options with `args:`, for example `args: [--fail-on, missing]`.

### From Python

```python
import wheelgap

targets = wheelgap.make_targets(python="3.13", platforms=["linux-aarch64", "musl-aarch64"])

result = wheelgap.check_package("numpy", "2.3.4", targets)  # looks the files up on PyPI
result.ok  # True if every target has a wheel
result.gaps  # the targets that don't
result.to_dict()  # {"name": ..., "results": {"3.13 musl-aarch64": "wheel", ...}}

for r in wheelgap.check_lockfile("uv.lock", targets):  # markers and the uv graph respected
    print(r.name, r.version, [t.platform for t in r.gaps])
```

`check_lockfile(..., offline=True)` never contacts an index: it raises `wheelgap.sources.SourceError` if an input has no locked file list and skips the re-check for Python versions outside the lock's `requires-python` (so those versions may show false gaps). It cannot be combined with `online=True`.

`check_packages([...])` checks many packages in parallel, and `check_package(..., files=[...])` works offline from filenames you already have.

To count packages that Pyodide ships itself, as `--pyodide-lock` does, pass `pyodide_locks=[wheelgap.pyodide.load_pyodide_lock("pyodide-lock.json")]` (a path or https URL, one lock per Python) to `check_packages()`, `check_package()` or `check_lockfile()`. They show as `bundled` on pyodide targets for the lock's Python, and the result's `bundled_version` is the version Pyodide ships. A different version from your pin or range still counts as `bundled` (`ok` stays `True`, as in the CLI), so check `bundled_version_differs`; `to_dict()` includes both as `"bundled"` and `"bundled_version_differs"`. A lock that no pyodide target can use raises `PyodideLockError`, as on the command line.

### Scanning the top PyPI packages

```bash
python -m wheelgap.scan --top 5000 --out coverage.json
```

This checks the latest release of the most-downloaded packages (from [top-pypi-packages](https://hugovk.dev/top-pypi-packages/)) against the eight common platforms (and Pyodide), for Python 3.13, 3.14, 3.14t, 3.15 and 3.15t by default, and writes one JSON file with per-platform totals. It's the data behind the [coverage tracker on the roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

Add `--pyodide-lock PATH_OR_URL` once per Pyodide release (each is for one Python) to count the packages Pyodide ships itself as `bundled` on that Python's `pyodide` target; the per-target `summary` counts them too, and `pyodide_locks` in the JSON lists the locks used. Pyodide targets with no lock get a note on stderr.

### Comparing two lockfiles

```bash
wheelgap diff old/uv.lock uv.lock -p 3.13 -t linux-aarch64,musl-aarch64
```

This checks both lockfiles and lists the packages that got worse on a target (a wheel that became a source build, say) and the new packages with gaps, so a dependency bump that drops a platform fails CI. It exits 1 if there are any, 0 if not, and 2 on bad input. A package that can no longer be checked counts as worse: `unknown`, or now installed from git, a path, a URL or a private index (a new one of those counts as a new gap). Packages are matched by name, so a requirements-file path with no name (`./pkg`, or `-e ./pkg` without `#egg=`) shows up as a new package rather than as the one it replaced. The same `-p` and `-t` apply to both lockfiles, and `[tool.wheelgap]` and `.python-version` aren't read yet, so pass them explicitly. `--json` prints one object with `worse` (each package's `old_versions` and `new_versions` lists and, per target, `old_status` and `new_status`), `new` (each new package's `versions` and per-target `status`), `unchecked` and `summary` counts; a skipped cell has the status `skipped` and a `skip_reason` (`git`, `url`, `private index`, ...). `diff` is a subcommand only as the first argument when there's no file or folder named `diff` in the current directory (then it's read as an input, with a note; `python -m wheelgap.diff` always compares, and a pinned `diff==2023.12.6` checks the PyPI package of that name). `python -m wheelgap.diff` works the same way.

### With an AI coding agent

wheelgap ships a **`check-wheels` skill** that teaches coding agents to run it, read its JSON and suggest fixes. It follows the open [Agent Skills](https://agentskills.io/) format.

**Claude Code** (install from this repository as a plugin):

```text
/plugin marketplace add wheelgap/wheelgap
/plugin install wheelgap@wheelgap
```

On Claude Code 2.1.275 or later, `/plugin install wheelgap --marketplace wheelgap/wheelgap` does both in one step.

**Codex, Cursor, GitHub Copilot and others:** copy [`integrations/claude-code/skills/check-wheels/`](integrations/claude-code/skills/check-wheels/) into your project's `.agents/skills/` folder. For a Cursor project rule instead, copy [`integrations/cursor/wheelgap.mdc`](integrations/cursor/wheelgap.mdc) to `.cursor/rules/`.

**As an MCP server** (new, not yet released): `wheelgap-mcp` lets any agent that speaks the [Model Context Protocol](https://modelcontextprotocol.io/) call wheelgap as a tool, with no shell access needed. It runs over stdio and has four read-only tools: `check_package` (one package, latest or a given version), `check_lockfile`, `check_dockerfile` and `list_targets`. `check_lockfile` gives the same answer as `wheelgap PATH --json`, `[tool.wheelgap]` settings included: it returns the packages with gaps, the ones it skipped (git, path and private-index packages, which aren't checked) and any notes. `check_dockerfile` gives the answer of `wheelgap PATH --dockerfile DOCKERFILE --json` (`PATH` defaults to the Dockerfile's folder), plus the `image` it read (`python`, `platform`, `glibc`, `musl`), so an agent can ask before switching `python:3.13-slim` to `-alpine` or adding `--platform=linux/arm64`; a Dockerfile wheelgap can't read is a tool error. On `pyodide` targets, `check_package` and `check_lockfile` take an optional `pyodide_lock` (a path or https URL of a `pyodide-lock.json`, as `--pyodide-lock` does), so packages Pyodide ships itself show as `bundled`. Tool calls send progress notifications when the client asks for them and don't stop the server answering `ping`. A cancelled call sends no more progress and no result, but its index lookups still finish before the next call starts. Like wheelgap, it needs nothing beyond `packaging`. Until the next release, run it from GitHub; for Claude Code:

```bash
claude mcp add wheelgap -- uvx --from git+https://github.com/wheelgap/wheelgap wheelgap-mcp
```

Other clients take the same command (`uvx`) and arguments in their MCP settings. Pass `check_lockfile` an absolute path, since the server's working directory may not be your project.

Contributing to wheelgap with an agent? See [AGENTS.md](https://github.com/wheelgap/wheelgap/blob/main/AGENTS.md).

## How it works

- **Version ranges** such as `numpy>=1.26,<2` use the newest matching release with a non-yanked file whose `Requires-Python` permits the selected Python. This applies when every target uses the same Python major/minor version (including a mix of regular and free-threaded builds). Missing or invalid `Requires-Python` metadata is treated as unrestricted. Runs checking several Python versions still choose the newest release matching the range without filtering by Python; per-Python resolution remains planned in [#84](https://github.com/wheelgap/wheelgap/issues/84). Exact pins and bare package names keep their existing behavior.
- **Lockfiles are read offline.** `uv.lock`, `pylock.toml`, `poetry.lock` and `pdm.lock` already record every file published for each pinned version (except a `uv.lock` or `pylock.toml` whose `requires-python` leaves out the Python you check; see below). wheelgap parses the wheel filenames' tags with [`packaging`](https://packaging.pypa.io/) and checks them against the exact tag set each target accepts. That is the same logic pip and uv use to pick a wheel.
- **`requirements.txt`, `requirements.lock` and bare package names** are looked up on the PyPI JSON API. Feed it a fully pinned file (from Rye, `uv pip compile`, `pip-compile` or `uv export`): wheelgap checks the lines you give it and doesn't resolve dependencies. Rye's default (non-universal) locks are resolved for the machine that created them and carry no platform markers. Constraint files (`-c FILE`, `-cFILE`, `--constraint FILE` or `--constraint=FILE`) restrict unpinned requirements without adding packages. Exact constraints select a version; ranges are intersected. A constraint wheelgap can't apply leaves the package `unknown` rather than guessed: one with an environment marker (it can't yet be applied per target), a URL or path, or two different exact versions. Constraint files given as a URL or an environment variable (`-c https://…`, `-c ${PIP_CONSTRAINT}`) are skipped.
- **`Pipfile.lock`** reads `default`, `develop` and any custom package categories (such as `docs` or `tests`) and looks up distribution filenames on PyPI for their pinned versions. Environment markers are preserved; Git, path, file and editable dependencies are skipped as non-index packages. Packages pinned to an `index` that isn't PyPI are skipped too (`private index`), so their names aren't sent to pypi.org. A PyPI mirror counts as a private index here, since wheelgap can't tell it apart from an internal one, unless `WHEELGAP_INDEX_URL` points at the JSON API of that same index (for a source `https://mirror.example.com/simple`, set `WHEELGAP_INDEX_URL=https://mirror.example.com/pypi`): then those packages are looked up there, on the index the lock already names. It must be the same repository, not just the same host: `<repo>/simple` matches `<repo>/pypi` with the same scheme, host and port, so another repository on the same Artifactory or Nexus server stays skipped. The index has to serve PyPI's JSON API, including pinned versions (`<WHEELGAP_INDEX_URL>/<name>/<version>/json`); on a mirror that only serves the simple API, such as the Tsinghua or Aliyun mirrors, those packages come back as lookup errors like `not on index` instead. `--index-url` lookups don't use this.
- **Environment markers are respected.** A `pywin32 ; sys_platform == 'win32'` dependency won't show up as a Linux gap. For `uv.lock` the dependency graph is walked per target, so transitive platform-specific packages are handled too. On `win32`, `linux-i686`, `musl-i686`, `linux-armv7l` and `musl-armv7l` a `platform_machine` comparison counts as unknown, so the package is still checked. `platform_machine` reports the machine, not the Python: 32-bit Python on 64-bit Windows says `AMD64`, a `linux/386` container on an x86_64 host says `x86_64`, and 32-bit Raspberry Pi OS on a Pi 4 or 5 (which boots a 64-bit kernel) or a `linux/arm/v7` container on an arm64 host can say `aarch64`.
- `--online` re-checks lockfile packages against PyPI. Packages the lock says came from another index keep their locked files, so their names are never sent to PyPI. Use it when a project has uploaded new wheels for an existing version since you locked, or if your `uv.lock` was generated with `environments` restrictions. A `uv.lock` or `pylock.toml` whose `requires-python` leaves out a target's Python (say `==3.13.*`, checked with `-p 3.14t`) may be missing wheels for it, because uv only keeps the wheels that can serve `requires-python`. Without `--online`, wheelgap then also looks up its PyPI packages on the index, keeps the lock's own files if a lookup fails, and prints a note (with `-q`, only when some packages couldn't be re-checked).
- `--offline` never contacts an index, for CI and coding agents without network. Inputs that need a lookup (requirements files, `pyproject.toml`, `Pipfile.lock`, package names, lockfile entries with no file list) make it exit 2 with an error naming the packages, so use a lockfile instead. A `--pyodide-lock` URL is refused too: pass a downloaded copy. The `requires-python` re-check above is skipped, with a note that those Pythons may show false gaps. It can't be combined with `--online`. Set `offline = true` in `[tool.wheelgap]` to make it the project default; `--online` on the command line overrides it for one run.
- **Cache:** lookups of a pinned version (`name==1.2.3`) are saved for 24 hours, so a repeat run needn't ask PyPI again. They're kept in `wheelgap/pinned-v1` inside `~/.cache` on Linux (or `$XDG_CACHE_HOME`), `~/Library/Caches` on macOS and `%LOCALAPPDATA%` on Windows. Each entry holds only the release's file names and yanked flags, named by a hash of the index URL (without any credentials in it), the package and the version; on Linux and macOS, wheelgap creates the folder and its entries readable by you only. `--online` always asks the index again and saves the fresh answer, so later runs use it; `--no-cache` neither reads nor saves the cache for one run. Entries older than 24 hours are deleted, at most once a day, and `wheelgap --clear-cache` deletes all of them (add inputs to check them right after). Both touch only wheelgap's own entry files, and never follow a link. Unpinned names, version ranges and `--index-url` lookups aren't cached, and neither are lookups from the Python API.
- **Private indexes:** `--index-url URL` (or `index-url` in `[tool.wheelgap]`) sends the PyPI lookups described above to a simple index that serves the JSON form (PEP 691) instead. The URL must be https (plain http only for localhost). Credentials can go in the URL passed to `--index-url` (`https://user:token@host/simple`) and never appear in output; in CI, `--index-url "$PRIVATE_INDEX"` keeps them out of your repo. They're refused in `[tool.wheelgap]`, since `pyproject.toml` is committed. An index that only serves HTML (PEP 503) gets a clear error.

## Why this exists

Missing wheels show up late, in the most expensive place:

- Your `python:3.13-alpine` image on an ARM runner takes 25 minutes because two transitive dependencies don't publish `musllinux_aarch64` wheels, and QEMU is compiling them.
- A build fails with `error: Microsoft Visual C++ 14.0 or greater is required` on a teammate's Snapdragon laptop.
- You try free-threaded Python 3.14t and half your stack quietly falls back to source builds, because `abi3` wheels don't apply there.

Sites like [pyreadiness.org](https://pyreadiness.org) and [free-threaded wheels](https://hugovk.github.io/free-threaded-wheels/) track the top few hundred packages. wheelgap checks your packages, at your pinned versions, for your targets, in about a second, and it can run in CI.

## Roadmap

wheelgap is growing into **caniuse for Python wheels**: an open map of which packages have wheels for which platforms. The CLI stays focused, and around it come a nightly tracker of the most-used PyPI packages, a community knowledge base of workarounds, and campaigns to help upstream projects ship missing wheels. **[Read the roadmap and join the discussion →](https://github.com/wheelgap/wheelgap/discussions/43)**

Next up for the CLI, each with a first step written up and no PR yet:

- "nearest version with a wheel" hints ([#20](https://github.com/wheelgap/wheelgap/issues/20))
- version ranges: picking the newest release that supports each target's Python ([#84](https://github.com/wheelgap/wheelgap/issues/84))

The [roadmap](https://github.com/wheelgap/wheelgap/labels/roadmap) and [help wanted](https://github.com/wheelgap/wheelgap/labels/help%20wanted) labels have more.

## Contributing

Contributions are welcome, and pull requests usually get a review within hours.

- **New here?** Issues labelled [good first issue](https://github.com/wheelgap/wheelgap/labels/good%20first%20issue) say which files to change and what to test.
- **Done one or two?** [help wanted](https://github.com/wheelgap/wheelgap/labels/help%20wanted) issues are bigger, and a good next step.

[CONTRIBUTING.md](https://github.com/wheelgap/wheelgap/blob/main/CONTRIBUTING.md) has the 3-command dev setup, or you can start in the browser with no setup:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/wheelgap/wheelgap)

Questions are welcome in [Discussions](https://github.com/wheelgap/wheelgap/discussions).

## Thanks

wheelgap is created and maintained by [@patrickballard](https://github.com/patrickballard), with a lot of help from these people. Thank you, all of you!

[![wheelgap's contributors](https://contrib.rocks/image?repo=wheelgap/wheelgap)](https://github.com/wheelgap/wheelgap/graphs/contributors)

## License

[MIT](https://github.com/wheelgap/wheelgap/blob/main/LICENSE)
