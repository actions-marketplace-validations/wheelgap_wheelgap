# Set up wheelgap-mcp in your agent

`wheelgap-mcp` is wheelgap's [MCP](https://modelcontextprotocol.io/) server. It
runs over stdio and gives an agent five read-only tools: `check_package`,
`check_lockfile`, `check_dockerfile`, `diff_lockfiles` and `list_targets`. Every
client below starts it with the same command:

```bash
uvx --from wheelgap wheelgap-mcp
```

That needs [uv](https://docs.astral.sh/uv/getting-started/installation/). Pick
your client, paste the snippet, restart the client (or reload its MCP servers)
and try the prompt at the end.

## Before the next release

The server isn't in a release yet: wheelgap 0.8.0 on PyPI has no `wheelgap-mcp`
command, so `uvx --from wheelgap wheelgap-mcp` fails with "An executable named
`wheelgap-mcp` is not provided by package `wheelgap`". Until the next release,
run it from GitHub. In each snippet, replace the arguments:

| Snippet type | Released | Before the release |
|---|---|---|
| Command line | `uvx --from wheelgap wheelgap-mcp` | `uvx --from git+https://github.com/wheelgap/wheelgap wheelgap-mcp` |
| JSON `args` | `["--from", "wheelgap", "wheelgap-mcp"]` | `["--from", "git+https://github.com/wheelgap/wheelgap", "wheelgap-mcp"]` |
| TOML `args` | `["--from", "wheelgap", "wheelgap-mcp"]` | `["--from", "git+https://github.com/wheelgap/wheelgap", "wheelgap-mcp"]` |

## Claude Code

Add it for yourself, in the current project:

```bash
claude mcp add wheelgap -- uvx --from wheelgap wheelgap-mcp
```

To share it with everyone working on the project, add `--scope project`
(`claude mcp add --scope project wheelgap -- ...`), or commit this `.mcp.json`
at the project root. Claude Code asks each person to approve a project server
before it uses it.

```json
{
  "mcpServers": {
    "wheelgap": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "wheelgap", "wheelgap-mcp"]
    }
  }
}
```

Check it with `claude mcp get wheelgap`. Docs: [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Cursor

Put this in `.cursor/mcp.json` in your project, or in `~/.cursor/mcp.json` to
have it in every project:

```json
{
  "mcpServers": {
    "wheelgap": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "wheelgap", "wheelgap-mcp"]
    }
  }
}
```

Docs: [Cursor MCP](https://cursor.com/docs/mcp).

## VS Code (GitHub Copilot)

Copilot reads the same `mcpServers` format as Claude Code. Put the snippet
below in `.mcp.json` at the workspace root to share it with your team, or in
`~/.copilot/mcp-config.json` (`$COPILOT_HOME/mcp-config.json` if you set
`COPILOT_HOME`) to have it in every workspace. **Chat: Open Customizations** >
**MCP Servers** > **Add Server** writes the same entry for you.

```json
{
  "mcpServers": {
    "wheelgap": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "wheelgap", "wheelgap-mcp"]
    }
  }
}
```

These files are read by the **Copilot** harness: pick it in the chat's harness
picker if it isn't already selected. The workspace-root `.mcp.json` is the same
file Claude Code reads, so one committed file serves both. The older Local
harness reads `.vscode/mcp.json` instead, with a top-level `servers` key in
place of `mcpServers`; VS Code's docs call that format deprecated (to be removed
in February 2027), so use it only if you're still on the Local harness. Docs:
[VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## OpenAI Codex CLI

```bash
codex mcp add wheelgap -- uvx --from wheelgap wheelgap-mcp
```

Or add it to `~/.codex/config.toml` (or a project's `.codex/config.toml`, which
Codex reads only in trusted projects):

```toml
[mcp_servers.wheelgap]
command = "uvx"
args = ["--from", "wheelgap", "wheelgap-mcp"]
```

Docs: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp).

## Claude Desktop

Open **Settings** > **Developer** > **Edit Config**, or edit
`claude_desktop_config.json` directly:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "wheelgap": {
      "command": "uvx",
      "args": ["--from", "wheelgap", "wheelgap-mcp"]
    }
  }
}
```

Quit Claude Desktop completely and reopen it. It doesn't start from your shell,
so if it can't find `uvx`, put the full path from `which uvx` (`where uvx` on
Windows) in `command`: usually `~/.local/bin/uvx` (uv's installer, written out in
full, e.g. `/Users/you/.local/bin/uvx`) or `/opt/homebrew/bin/uvx` (Homebrew). On
Windows, double the backslashes in JSON: `"C:\\Users\\you\\.local\\bin\\uvx.exe"`. The server's stderr goes to `mcp-server-wheelgap.log` in
`~/Library/Logs/Claude` (macOS), `%APPDATA%\Claude\logs` (Windows) or
`~/.config/Claude/logs` (Linux). Docs:
[Connect to local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## Try it

Open a project that has a `uv.lock` and ask:

> Can this project's uv.lock install on Alpine ARM64 with Python 3.13?

The agent should call `check_lockfile` with the lockfile's path, `python`
`3.13` and the `musl-aarch64` target, and list any packages with no wheel for
it. The server's working directory may not be your project, so the agent should
pass an absolute path or a `file://` URI. A relative path such as `uv.lock`
works only when the client shares its
[roots](https://modelcontextprotocol.io/specification/2025-06-18/client/roots).
