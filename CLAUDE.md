@AGENTS.md

## Claude Code specifics

- The commands above are slash commands in `.claude/commands/`.
- Run the Step 1 scrape with `run_in_background` or a 10-minute timeout.
- `.claude/settings.json` blocks Apify MCP tools, olcli compile/PDF tools, LaTeX
  compilers and reads of bulky or secret files. Don't work around a denied call.
- Start a fresh chat with `/clear` before each `/tailor`.
