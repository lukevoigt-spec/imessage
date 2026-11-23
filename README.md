# iMessage Explorer (retired)

iMessage Explorer is retired. Everything it did now lives in
**[Brain Explorer](https://github.com/lukevoigt-spec/brain-explorer)**: the Messages tab
(search, threads, the pattern screens, senders and labels), "Read by Claude" (the full
read, digests, histories, the portrait, plans, the monthly check, the report and Ask), each
person's page, and the MCP connection for AI tools
(`python3 -m brainexplorer.messages.mcp_server`).

Brain Explorer reads and writes the same data folder, `~/.imessage-explorer`, so nothing
made with iMessage Explorer is lost or paid for again.

What is left here:

- `docs/withings.html` and `docs/whoop.html`: the public sign-in relay pages that
  Brain Explorer's Withings and WHOOP connections use (served by GitHub Pages). Keep them.
- `uninstall.sh`: removes the old app from a Mac and keeps your data:
  `bash uninstall.sh` (add `--delete-my-data` only if you also want the data folder gone,
  which Brain Explorer needs).

The old code is in this repository's history.
