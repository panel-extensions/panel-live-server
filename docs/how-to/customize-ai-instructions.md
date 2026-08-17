# Customize AI Instructions

When running as an MCP server, `pls` sends the AI a set of instructions telling it how to
use the tools: prefer HoloViz packages, present the returned URL as a Markdown link, and so
on. If those defaults do not match how your team works, you can customise individual
sections without forking the project.

Every section can be customised:

| Section | What it covers |
| --- | --- |
| `intro` | The one-line description of what the server does |
| `workflow` | How `show`, `screenshot`, `edit`, and `evaluate` relate, and that validation is automatic |
| `file_policy` | Not writing visualization code out to files |
| `library_selection` | Which plotting libraries the AI should reach for, and when to defer to the `developing-with-holoviz` skill |
| `rendering` | Panel reactive patterns, and clients that link out instead of embedding |
| `output` | How the returned URL is presented back to you |
| `errors` | What `SecurityError` and `ValidationError` mean |
| `screenshot` | What the AI looks for in a screenshot, both when checking its own draft and when answering questions about a chart you already have |

`library_selection` and `output` are the ones most people want; they are pure
preference. The others describe how the server actually behaves, so prefer adding to
them over replacing them — a replacement that contradicts the server (say, telling the
AI that validation must be requested) makes its output worse with nothing on screen to
explain why.

`screenshot` covers both ways the tool gets used, so a rule you add there reaches the
AI whether it is checking its own draft or answering a question about a chart you can
already see. The two have different built-in advice; replacing the section collapses
them to your one text.

## Adding your own rules

**Step 1 — create the file.** Put it anywhere you like, and include only the sections you
want to change. Here it is at `/home/you/my-prompts.json`:

```json
{
  "library_selection": "For sine and cosine waves use hvplot, coloured pink."
}
```

**Step 2 — point `pls mcp` at it** by adding `--prompts` and the path to your existing MCP
configuration:

=== "VS Code"

    In `.vscode/mcp.json`, add to `args`:

    ```json
    {
      "servers": {
        "panel-live-server": {
          "type": "stdio",
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Cursor"

    In `~/.cursor/mcp.json`, add to `args`:

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Claude Desktop"

    In the config file for your OS:

    - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
    - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
    - **Linux:** `~/.config/Claude/claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "panel-live-server": {
          "command": "/path/to/pls",
          "args": ["mcp", "--prompts", "/home/you/my-prompts.json"]
        }
      }
    }
    ```

=== "Claude Code"

    ```bash
    claude mcp add panel-live-server -- /path/to/pls mcp --prompts /home/you/my-prompts.json
    ```

    If the server is already registered, remove it first with
    `claude mcp remove panel-live-server`.

=== "claude.ai"

    Add the flag to the command you start the HTTP server with:

    ```bash
    /path/to/pls mcp --transport http --port 8001 --prompts /home/you/my-prompts.json
    ```

**Step 3 — restart the MCP server** in your client. The instructions are read once at
startup, so nothing changes until it restarts.

Your text is **added in front of** the built-in text for that section, under a header
marking it as authoritative. Your rules are read first and win where they conflict, while
the defaults stay in place below them as the fallback, so the AI still knows things like
which libraries may not be installed in this environment.

Include only what you want to change. Leave out `output` and it keeps its built-in text,
and keeps tracking upstream as you upgrade. That is the reason to customise a section
rather than copy the whole prompt: the parts you did not touch keep improving.

Writing `{"add": "..."}` instead of a bare string means exactly the same thing, if you
prefer being explicit.

## Replacing a section outright

If you want the built-in text gone rather than added to, say so explicitly:

```json
{
  "library_selection": {"replace": "Use plotly.express and nothing else."}
}
```

Use this sparingly. The default `library_selection` also tells the AI that Matplotlib,
Plotly, seaborn and Altair may not be installed, so replacing it means the AI can no longer
warn you before reaching for a package that is missing. Adding is usually what you want;
replacing is for when the default actively contradicts your policy.

## Things worth knowing

**Restart after editing.** The instructions are read once when the MCP server starts, so
changes to your prompts file only take effect after your AI client restarts the server.

**Use an absolute path**, or one starting with `~`. A relative path is resolved against the
server's working directory, which your MCP client chooses and you do not control, so it may
silently fail to load.

---

## Next Steps

- [Run as MCP Server](run-as-mcp-server.md): connect to your AI client
- [Troubleshoot Issues](troubleshooting.md): common problems and solutions
