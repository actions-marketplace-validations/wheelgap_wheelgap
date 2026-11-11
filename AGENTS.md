# AGENTS.md

Instructions for coding agents (Codex, Claude Code, Copilot, Cursor and others) and anyone else working on wheelgap. [CONTRIBUTING.md](CONTRIBUTING.md) has the same rules for people, in more words.

## What wheelgap does

For each package in a lockfile and each target platform, wheelgap decides whether pip or uv would install a prebuilt wheel, build from source, or fail. **A wrong answer is the worst bug.** Where wheelgap can't be sure, it must give an error (or `unknown`), never a guess.

## Setup and checks

You need [uv](https://docs.astral.sh/uv/). Run all four before you push:

```bash
uv run --group dev ruff format .
uv run --group dev ruff check .
uv run --group dev mypy
uv run --group dev pytest
```

- One test: `uv run --group dev pytest tests/test_dockerfile.py -k parse_from`.
- One Python: `uv run --python 3.14t --group dev pytest`.
- CI runs pytest on Linux (Python 3.11–3.14 and 3.14t) and on macOS and Windows (3.14), plus ruff and mypy on Linux.
- Try a change by hand, offline: `uv run wheelgap tests/fixtures/uvproj -p 3.13 -t all --explain`.

## Code map (`src/wheelgap/`)

| File | What it does |
| --- | --- |
| `cli.py` | Command line: arguments, `[tool.wheelgap]` config, text / `--json` / `--markdown` output, exit codes |
| `api.py` | Python API (`make_targets`, `check_package(s)`, `check_lockfile`) |
| `sources.py` | Reads inputs: `uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, requirements files |
| `targets.py` | Targets (Python × platform) and the wheel tags each accepts |
| `check.py` | The status per package and target (`wheel`, `source`, `missing`, `unknown`, `n/a`, `bundled`) and `--explain` reasons |
| `pypi.py` | The only HTTP client (`fetch_json`: retries, https-only redirects) and the PyPI lookups (`fill`, `fill_all`, `recheck_lock`) |
| `cache.py` | The 24-hour on-disk cache of pinned PyPI lookups (`--no-cache` and `--online` skip it) |
| `dockerfile.py` | `--dockerfile`: reads the final stage's image and platform the way Docker/BuildKit does |
| `offline.py` | `--offline`, `offline = true` and `check_lockfile(..., offline=True)`: refuse inputs that would need an index lookup |
| `pyodide.py` | `--pyodide-lock`: packages Pyodide ships itself |
| `scan.py` | `python -m wheelgap.scan`: the data behind the [coverage tracker](https://wheelgap.github.io/tracker/) |
| `diff.py` | `python -m wheelgap.diff OLD NEW`: packages that got worse between two lockfiles, and new packages with gaps |
| `mcp.py` | `wheelgap-mcp` / `python -m wheelgap.mcp`: an MCP server over stdio (stdlib JSON-RPC, no MCP SDK) with `check_package`, `check_lockfile`, `check_dockerfile` and `list_targets` tools |

## Tests

- **No network, ever.** Use the `fake_pypi` fixture in `tests/conftest.py`. To add a package, add it to `FAKE_INDEX`. The fixture returns the list of URLs fetched, so tests can assert what was (or wasn't) looked up. Lockfile fixtures live in `tests/fixtures/`; small one-off lockfiles go in `tmp_path`.
- `fake_pypi` patches `wheelgap.pypi.fetch_json`. New code that fetches should call `pypi.fetch_json` at call time (as `pyodide.py` does), not import the name, so the fixture catches it.
- **Every behaviour change gets a test that fails without the change.** Check this by reverting your source change and running the test.
- **Write test files that contain non-ASCII text with `encoding="utf-8"`** (or use `write_bytes`). Windows CI otherwise writes cp1252.
- Put new cases in the existing parametrized tables where one fits, e.g. `test_dockerfiles_docker_rejects` and `test_parse_from` in `tests/test_dockerfile.py`.

## House rules

- **Dependencies:** standard library and `packaging` only.
- **All network access goes through `pypi.fetch_json`.**
- **Wheel tags:** use `packaging.tags` where it has an API, and mirror what pip accepts. The manylinux/musllinux platform lists in `targets.py` follow pip's rules; keep them that way.
- **`--dockerfile` must match Docker exactly or raise `DockerfileError`.** When you port BuildKit behaviour, link the file and commit in a comment (see `_heredocs` in `dockerfile.py`).
- **Output:** `--json` keys are a public interface. Add keys; don't rename or remove them. Notes go to stderr, never into `--json` or `--markdown` stdout.
- **Style:** match the surrounding code; ruff's line length is 120. Don't reformat or refactor code your change doesn't need.
- **`CHANGELOG.md`:** if users would notice the change, add one bullet at the top of `## Unreleased`, written for users, ending with the **PR** number and your handle: `(#123, thanks [@you](https://github.com/you))`. Add it once the PR is open and you know the number. Docs-only changes get no entry, and released sections are never edited.
- **Agent integrations:** when a flag, JSON key, status or exit code changes, update `integrations/claude-code/skills/check-wheels/SKILL.md` and `integrations/cursor/wheelgap.mdc` too, and only describe what's already released on PyPI.
- **README:** update it when users would notice the change (a new flag, target, input format or config key). A new input format also needs the `files` pattern in `.pre-commit-hooks.yaml`.

## Picking work and opening a PR

- Issues labelled `good first issue` say which files to change and which tests to add. Do the **first step** an issue describes; the "Later" items are separate PRs.
- **Check the assignee and existing PRs before you start.** Whoever asks gets an issue assigned for 18 hours from the assignment, and keeps it by opening a PR that links the issue (a draft is fine) within that time. While it's held for someone else, pick another issue. If the 18 hours pass with no PR, the maintainers fix the issue themselves, unless another PR is already waiting, which they review instead. An issue nobody has asked for goes to the first PR opened for it.
- In the PR description, use `Closes #N` only if the PR finishes the issue; otherwise write `Part of #N`.
- The maintainer may push fixes to your branch before merging, so leave "Allow edits by maintainers" on.
- Say in the PR description which AI tool helped (e.g. "Made with Codex"). The person opening the PR must have read and run the change. One issue per PR.
- Changes to `.github/`, `action.yml`, `pyproject.toml`, `uv.lock` or `src/wheelgap/pypi.py` get extra scrutiny, so expect them to take longer.

## No issue fits? Find a real bug

A wrong answer from wheelgap that pip proves wrong is the most useful report there is. You may open a bug report for one yourself, and then fix it.

1. Run wheelgap with `--explain` on a real lockfile, such as a popular open-source project's `uv.lock`, `poetry.lock` or pinned `requirements.txt`, across a few targets: `uvx wheelgap uv.lock -t all -p 3.13 -p 3.14t --explain`.
2. Check a cell you doubt with pip, which has the last word. pip takes `manylinux` and `musllinux` tags literally, so pass every tag the target accepts, from its glibc or musl version down. For `linux-x86_64` with wheelgap's default glibc (2.36), in bash:

   ```bash
   platforms="$(for m in $(seq 36 -1 5); do printf -- '--platform manylinux_2_%s_x86_64 ' "$m"; done)"
   platforms+="--platform manylinux2014_x86_64 --platform manylinux2010_x86_64 --platform manylinux1_x86_64"
   pip download --no-deps --only-binary=:all: --index-url https://pypi.org/simple -d /tmp/wg-check \
     $platforms --python-version 3.14 --implementation cp --abi cp314t numpy==2.3.4
   ```

   - **Other targets:**
     - for `linux-aarch64` and the other Linux arches, change the arch and stop the loop at 17 (`seq 36 -1 17`), with only `manylinux2014_<arch>` after it, since there are no older manylinux tags for them; for `linux-armv7l`, also pass `--platform linux_armv7l`;
     - for `musl-aarch64`, pass `--platform musllinux_1_2_aarch64 --platform musllinux_1_1_aarch64`;
     - for Windows, pass one tag (`win_amd64`, `win_arm64`);
     - for macOS, pass the target's version (`macosx_14_0_arm64` for wheelgap's default, `--macos 14.0`). pip widens macOS tags to older versions itself.
   - **Other glibc or musl versions:** if you pass `--glibc` or `--musl` to wheelgap, start the list from that version. `wheelgap --list-targets` shows each target's floor.
   - **ABI:** use `--abi cp314t` for free-threaded Python and `--abi cp314` otherwise. pip adds `abi3` and `none` itself where they apply.
   - **Reading the result:**
     - if pip downloads a wheel, wheelgap should say `wheel`;
     - if pip says "No matching distribution found", wheelgap should say `source` or `missing`. This check can't tell those two apart.
3. **Open an issue only when wheelgap and pip disagree** and no open issue covers it already. Use the [bug report](https://github.com/wheelgap/wheelgap/issues/new?template=bug_report.yml) template with both commands and both outputs.
4. Limits:
   - one open self-filed issue per person at a time;
   - feature ideas go to [Discussions → Ideas](https://github.com/wheelgap/wheelgap/discussions/categories/ideas), not issues;
   - say which AI tool helped find it.
5. To fix it yourself, say so in the issue and it's assigned to you for 18 hours, as usual. An obvious one-line fix can go straight to a PR instead, as CONTRIBUTING.md allows for small fixes.

Don't file:
- gaps pip agrees with (the package really lacks that wheel; the [tracker](https://wheelgap.github.io/tracker/) lists those);
- Pyodide, iOS or Android cells, which pip can't check this way;
- `n/a`, `bundled` and `unknown` cells (`--no-deps` doesn't evaluate markers, and pip doesn't know what Pyodide bundles);
- anything you haven't run yourself.
