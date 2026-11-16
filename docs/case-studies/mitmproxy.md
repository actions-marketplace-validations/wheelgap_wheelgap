# mitmproxy: runtime dependencies on five build platforms

[mitmproxy](https://github.com/mitmproxy/mitmproxy) is an interactive HTTP proxy. This study checks its runtime dependencies at revision [`f7701d5f6b69e103bcc4995a641447ae70574e99`](https://github.com/mitmproxy/mitmproxy/tree/f7701d5f6b69e103bcc4995a641447ae70574e99), using wheelgap **0.10.0** and package files available on **15 November 2026**.

## Targets from the project's build configuration

The pinned [binary-build matrix](https://github.com/mitmproxy/mitmproxy/blob/f7701d5f6b69e103bcc4995a641447ae70574e99/.github/workflows/main.yml#L69-L96) names Linux x86_64 and ARM64, macOS Intel and Apple silicon, and Windows. [`.python-version`](https://github.com/mitmproxy/mitmproxy/blob/f7701d5f6b69e103bcc4995a641447ae70574e99/.python-version) selects Python 3.15. The Linux builds use Debian 12 containers, and the macOS runners are macOS 15, so this check uses glibc 2.36 and macOS 15.0.

`--no-dev` limits the check to runtime dependencies. It excludes the development, test and packaging tools installed by the full build workflow. The editable `mitmproxy` package itself is skipped: the question here is which dependencies need a source build on these targets.

## Reproduce the scan

Download the [pinned lockfile](https://github.com/mitmproxy/mitmproxy/blob/f7701d5f6b69e103bcc4995a641447ae70574e99/uv.lock) into an empty directory, then run:

```bash
curl -fL https://raw.githubusercontent.com/mitmproxy/mitmproxy/f7701d5f6b69e103bcc4995a641447ae70574e99/uv.lock -o uv.lock
uvx --from wheelgap==0.10.0 python -m wheelgap uv.lock \
  -p 3.15 \
  -t linux-x86_64,linux-aarch64,macos-x86_64,macos-arm64,win-amd64 \
  --glibc 2.36 --macos 15.0 --no-dev --explain --suggest
```

Output, with the individual rejected-wheel lists omitted:

```text
wheelgap 0.10.0 · uv.lock · 43 packages · Python 3.15

package       version  linux-x86_64  linux-aarch64  macos-x86_64  macos-arm64  win-amd64
cryptography  50.0.2   ✓             ✓              ● source      ✓            ✓
markupsafe    3.0.3    ● source      ● source       ● source      ● source     ● source

41 packages with wheels for every target hidden (--all to show)
...
cryptography 50.0.2: no newer release has a wheel for macos-x86_64
...
markupsafe 3.0.3 -> 3.0.4 has wheels for linux-x86_64, linux-aarch64, macos-x86_64, macos-arm64, win-amd64

skipped 1 non-index package: mitmproxy (editable)
linux-x86_64   1 from source
linux-aarch64  1 from source
macos-x86_64   2 from source
macos-arm64    1 from source
win-amd64      1 from source
2 of 43 packages have gaps: 2 will build from source.
```

The command exits **1**, the expected result when gaps are found.

## MarkupSafe 3.0.3: a Python-version gap

The dependency path is `mitmproxy → flask → markupsafe`. On Linux x86_64, for example, `--explain` rejects this wheel:

```text
markupsafe-3.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
built for cp314-cp314, not CPython 3.15
```

The same Python-version gap affects all five targets. `--suggest` finds **3.0.4**, which has wheels for all of them. Updating the lockfile to that release through the project's normal resolver, followed by compatibility tests, is one way to remove this wheel-availability gap. Keeping the current pin requires checking its source-build path in CI.

## cryptography 50.0.2: a macOS architecture gap

The dependency path is `mitmproxy → cryptography`. This pin has a compatible wheel on four targets, but none for macOS Intel. For example, `--explain` rejects its Apple-silicon wheel:

```text
cryptography-50.0.2-cp315-abi3.abi3t-macosx_11_0_arm64.whl
macosx_11_0_arm64 doesn't match macos-x86_64
```

`--suggest` finds no newer release with an Intel macOS wheel at the time of this check. To retain this pin and target, validate a source build with the required toolchain in the packaging pipeline. The presence of an sdist gives a `source` result; wheelgap does not compile it or check that the build environment is sufficient.

## Independent checks with pip

pip **26.3** was run with `--dry-run --ignore-installed --no-deps --only-binary=:all:`, the public PyPI index, `--python-version 3.15 --implementation cp --abi cp315`, and each target's platform tags. The observations agreed with wheelgap:

| Package | Targets checked | pip result |
| --- | --- | --- |
| MarkupSafe 3.0.3 | All five | No matching distribution found |
| MarkupSafe 3.0.4 | All five | Would install MarkupSafe-3.0.4 |
| cryptography 50.0.2 | macOS Intel | No matching distribution found |
| cryptography 50.0.2 | macOS Apple silicon | Would install cryptography-50.0.2 |

For example, reproduce the Intel macOS check with:

```bash
uvx --from pip==26.3 python -m pip --isolated install \
  --dry-run --ignore-installed --no-deps --only-binary=:all: \
  --index-url https://pypi.org/simple \
  --python-version 3.15 --implementation cp --abi cp315 \
  --platform macosx_15_0_x86_64 'MarkupSafe==3.0.3'
```

That reports `No matching distribution found`. Changing the pin to `MarkupSafe==3.0.4` reports `Would install MarkupSafe-3.0.4`. With `cryptography==50.0.2`, it again finds no wheel; changing the platform to `macosx_15_0_arm64` finds one.

For the Linux checks, pip received every manylinux tag the target accepts: glibc 2.36 down to 2.5 plus the legacy 2014/2010/1 tags on x86_64, and down to 2.17 plus manylinux2014 on aarch64. Passing only a single manylinux tag would miss older compatible wheels. pip expands the macOS tags to older compatible versions itself; Windows uses `win_amd64`.

These were cross-platform artifact checks from Windows with CPython 3.14.6. They inspect published files and metadata without installing or compiling mitmproxy. A successful source build and the project's runtime behavior need their own validation.
