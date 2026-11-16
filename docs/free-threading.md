# Wheels on free-threaded Python

Free-threaded CPython — the `t` builds, `3.13t` and `3.14t` — runs without the
global interpreter lock (GIL), so threads can execute Python code in parallel.
Checking `-p 3.14t` asks whether a wheel for that build exists: extension wheels
are tagged `cp314t` (or `abi3t`), and `abi3` wheels don't apply. A `wheel`
result means pip installs the package on a free-threaded interpreter instead of
compiling it.

## A wheel isn't the whole answer

What a wheel filename can't say is whether the extension inside declares it can
run without the GIL (a `Py_mod_gil` slot, or `PyUnstable_Module_SetGIL` for a
single-phase module). An extension that doesn't declare support still installs
and imports on a free-threaded build — but CPython turns the GIL back on for the
whole process when it's imported, so every thread serializes again. The only
sign is a `RuntimeWarning` on import:

```text
RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module
'grpc._cython.cygrpc', which has not declared that it can run safely without the
GIL. To override this behavior and keep the GIL disabled (at your own risk), run
with PYTHON_GIL=0 or -Xgil=0.
```

A clean `-p 3.14t` table means the packages install on free-threaded Python, not
that they keep it free-threaded.

## See what the build lab recorded

The [coverage tracker's build lab](https://wheelgap.github.io/tracker/) builds
popular packages that have no wheel for a target from their sdist on GitHub's
runners, then imports the result. On a free-threaded target it also records
whether importing turned the GIL back on, and names the module CPython blamed
(`gil: re-enabled` in its data, with the module next to it).

With `--explain --notes` (0.11.0 and later), a `source` cell gets the lab's
verdict as one line when the lab built exactly the checked version for exactly
that target — see [build lab results](output.md#build-lab):

```text
grpcio 1.85.1 on linux-x86_64: build lab (2026-11-15, ubuntu-24.04, Python 3.14.10t): builds in 13 min, but importing it turns the GIL back on
zstandard 0.25.0 on linux-x86_64: build lab (2026-11-15, ubuntu-24.04, Python 3.14.10t): builds from source in 60 s and imports, GIL stays off
```

The lab only covers packages it built from source; a prebuilt wheel whose
extension doesn't declare support turns the GIL back on just the same.

## Check your own environment

On a free-threaded build, watch for the `RuntimeWarning` above when your
application or test suite imports its dependencies. It fires once per
interpreter (`-v` names every such import), so run it as a failure to be sure:

```bash
python3.14t -W error::RuntimeWarning -m pytest
python3.14t -X gil=0 -m pytest
```

- `-W error::RuntimeWarning` turns the warning into an error that names the
  module which turned the GIL back on, so a CI run can't miss it.
- `-X gil=0` (or `PYTHON_GIL=0`) keeps the GIL disabled even when a
  non-declaring extension is imported, so the run exercises real
  free-threading. The trade-off is in the warning itself: the extension then
  runs without the lock it may rely on, at your own risk. Run it without
  `-X gil=0` too when you want the warnings that name those modules.

The [free-threading porting guide](https://py-free-threading.github.io/porting/)
covers what declaring support involves when the module is yours.

## Examples from the build lab

The lab ran these builds on `ubuntu-24.04` and `windows-11-arm` on
[2026-11-15](https://github.com/wheelgap/tracker/actions/runs/43743579096).
Each built from its sdist and imported, but the import turned the GIL back on.
These are results for those exact versions and Pythons.

| Package | Target | Module CPython named |
| --- | --- | --- |
| `grpcio` 1.85.1 | `3.14t linux-x86_64` | `grpc._cython.cygrpc` |
| `psycopg2-binary` 2.9.13 | `3.14t linux-x86_64` | `psycopg2._psycopg` |
| `duckdb` 2.0.0 | `3.14t linux-x86_64` | `_duckdb` |
| `spacy` 3.8.18 | `3.14t linux-x86_64`, `3.14t win-arm64` | `thinc.backends.numpy_ops` (a `thinc` extension) |
| `brotli` 1.2.1 | `3.14t linux-x86_64`, `3.14t win-arm64` | `_brotli` |

In the same day's [other run](https://github.com/wheelgap/tracker/actions/runs/43750127182),
`zstandard` 0.25.0 built from source and kept the GIL off.
