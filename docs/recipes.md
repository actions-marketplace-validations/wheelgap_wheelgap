# Recipes

Practical ways to check your project's dependencies with wheelgap.

## Multi-arch images

Check both architectures before building an image for AMD64 and ARM64. This
example uses `uv.lock` and a `Dockerfile` whose final stage is based on an
official `python` image or a Python-tagged `ghcr.io/astral-sh/uv` image, such as
`FROM python:3.13-slim-bookworm`, without pinning it to one architecture. Run it from the build context directory:

```bash
wheelgap uv.lock --dockerfile Dockerfile -t linux/amd64,linux/arm64 &&
  docker buildx build --platform linux/amd64,linux/arm64 \
    --output type=oci,dest=app.tar .
```

- The same Docker platform names select both architectures in wheelgap and
  Buildx. `--dockerfile` supplies the Python version and libc settings from
  the final image; for a supported Alpine image it checks musl wheels instead.
- `&&` starts the build only if the check succeeds. With the default failure
  policy (unless `fail-on` is set in `pyproject.toml`), a source build, missing
  distribution or unknown result on either architecture stops it. Input errors
  stop it too.
- In a CI script that runs under `set -e`, keep this as the step's last
  command, or put `wheelgap` and `docker buildx build` on separate lines: a
  failed check on the left of `&&` doesn't end the script by itself.
- Use a Buildx builder configured for both platforms and OCI export, such as
  the `docker-container` builder in Docker's
  [multi-platform guide](https://docs.docker.com/build/building/multi-platform/).
  The [OCI output](https://docs.docker.com/reference/cli/docker/buildx/build/#oci)
  writes both image variants to `app.tar`.

The check reports wheel availability for the lockfile, not whether the image
build or application will succeed. Match any dependency-selection flags to
what your Dockerfile installs.

## Free-threading readiness

List packages that need a source build, have no usable distribution, or couldn't
be looked up on at least one common native platform for free-threaded Python 3.14. With `wheelgap`
and `jq` installed, run this in your project directory:

```bash
set -o pipefail
wheelgap -p 3.14t -t all --json --exit-zero \
  | jq -r '.packages[] | select([.results[]] | any(. == "source" or . == "missing" or . == "unknown")) | .name'
```

- `-p 3.14t` checks free-threaded Python; you do not need to run wheelgap on that
  interpreter.
- `-t all` checks the eight common native platforms. Replace it with the targets
  you deploy to if you only need a subset.
- `--exit-zero` keeps gaps from failing this informational command.
  Bash's `pipefail` still exposes input errors from wheelgap.
- The filter reads the **values** of `results`, so it keeps working if you add
  more Pythons (for example `-p 3.13,3.14t`), though the list then includes gaps
  on those Pythons too.

Sample output (from wheelgap's own `tests/fixtures/uvproj` test project):

```text
fastlib
nativeext
puresdist
winonly
```

This reports wheel availability, not whether a package is safe to use without
the GIL. A package listed because its result is `unknown` couldn't be looked up;
its `error` field in the JSON says why.

## Monorepos with separate lockfiles

When projects `a/` and `b/` each have a `uv.lock`, check both from the
repository root with the same Python and deployment platform:

```bash
wheelgap a/uv.lock b/uv.lock -p 3.13 -t linux-aarch64
```

This produces one report for both inputs. The report doesn't say which
lockfile a row comes from, and a package locked in both projects is listed once
for each; use the loop below to see gaps per project. It exits `1` when a gap
fails the check (see `--fail-on`) and `2` for bad input, so a CI job can fail
when either project needs attention.

Only `a/pyproject.toml`, next to the **first input**, supplies
`[tool.wheelgap]` defaults for this combined run; `b/pyproject.toml` does not.
The explicit `-p` and `-t` flags override those two defaults for both projects.
Other settings from the first project, such as `glibc`, `ignore`, `fail-on`,
`extras` and `groups`, still apply to both. Use the combined command when those settings
are intended to be shared.

To respect each project's own configuration instead, run a separate check
for each lockfile in a shell script or CI step:

```bash
for lockfile in a/uv.lock b/uv.lock; do
  wheelgap "$lockfile" || exit "$?"
done
```

Each invocation reads `[tool.wheelgap]` next to its own lockfile. Configure
the intended Python and targets there, or add `-p` and `-t` to the command.
The loop stops at the first failing check and exits with its code, so later
lockfiles aren't checked and a later successful check can't hide an earlier gap
or input error.
