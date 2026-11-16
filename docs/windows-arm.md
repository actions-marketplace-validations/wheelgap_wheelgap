# Python wheels on Windows on ARM

Native ARM64 Python on Windows needs wheels compatible with `win_arm64`, or
platform-independent wheels. A `win_amd64` wheel is for x64 Python, even when
that Python runs on the same ARM64 computer. GitHub's
[`windows-11-arm` runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
is an example of a native ARM64 target.

## When pip tries to compile a package

If pip can't find a compatible wheel and a source distribution is available,
it can try to build the package. Native extensions often need the
[MSVC ARM64 build tools and Windows SDK](https://learn.microsoft.com/en-us/visualstudio/install/visual-studio-on-arm-devices).
Some packages also need Rust or libraries such as OpenSSL built for ARM64.
Look above the final `Failed building wheel` message for the first error from
the compiler, build backend, or missing library.

## Check your project before installing

From the directory with your lockfile, pass the target and every Python version
you ship. This can run on a different architecture from the one being checked.

```powershell
wheelgap uv.lock -t win-arm64 -p 3.13 -p 3.14 --explain
```

No lockfile? To check whether `pip install NAME` would compile anything, pass the name with
[`--deps`](cli.md#deps), which also checks everything it installs and names the path to each gap:
`wheelgap mcp==2.3.0 --deps -t win-arm64 --explain` finds cryptography via `mcp → PyJWT → cryptography`.

Use `-p 3.14t` to check free-threaded 3.14 separately. In the report, `source`
means wheelgap found a source distribution but no matching wheel. It can't tell
from the filenames whether the source build will succeed. For the full list of
statuses, see [Output](output.md).

## Examples from the build lab

The [coverage tracker's build lab](https://wheelgap.github.io/tracker/data/lab.json)
attempted these builds on `windows-11-arm` on
[2026-11-15](https://github.com/wheelgap/tracker/actions/runs/43743579096).
These are results for those exact versions and Pythons.

| Package | Python | Lab result |
| --- | --- | --- |
| `cryptography` 50.0.2 | 3.14 and 3.14t | Source build failed. The lab recorded a missing OpenSSL dependency. |
| `pyarrow` 26.0.1 | 3.14 and 3.14t | Source build failed with a compile error. |
| `dbt-extractor` 0.6.0 | 3.14 and 3.14t | Built and imported on 3.14. Failed to compile on 3.14t. |

[PyPI's files for cryptography 46.0.3](https://pypi.org/project/cryptography/46.0.3/#files)
include Windows ARM64 wheels. The [46.0.4](https://pypi.org/project/cryptography/46.0.4/#files)
and [50.0.2](https://pypi.org/project/cryptography/50.0.2/#files) files don't.
This can affect projects that depend on it indirectly, for example through
`pyjwt[crypto]`.

## Choose a way forward

- Find a compatible release with [`--suggest`](output.md#suggest):
  `wheelgap uv.lock -t win-arm64 -p 3.14 --explain --suggest`. Check the proposed
  version against your dependency constraints, changelogs and security updates
  before pinning it, then refresh the lockfile and rerun the check.
- Build a native wheel. Install the ARM64 compiler and the dependencies listed
  in the package's own build instructions, such as
  [cryptography's Windows requirements](https://cryptography.io/en/latest/installation/#building-cryptography-on-windows).
  This preserves native execution but adds build time and toolchain upkeep.
- Use [x64 emulation on Windows 11 on ARM](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation).
  Install x64 Python in a separate environment and check it with `-t win-amd64`.
  This can make more wheels available at the cost of emulation overhead.
