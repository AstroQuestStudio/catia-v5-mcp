# Security policy

## Scope

This server drives a locally installed CATIA V5 through COM on behalf of an AI client. It has
no network listener (stdio only) and opens no ports.

Things worth knowing:

- **It can modify and delete files** the CATIA user can write (`SaveAs`, `catia_save_all`,
  `catia_export`). Give the agent a dedicated working folder and review destructive calls; the tools
  carry `destructiveHint` annotations for clients that support approval prompts.
- **Popup watchdog**: it only closes dialogs whose single button is OK/Close. It never answers a
  question (Yes/No, Save?). Disable it with `CATIA_MCP_WATCHDOG=0`.
- **Hang guard kill** (`CATIA_MCP_HANG_KILL=1`) terminates CATIA and loses unsaved work; it is off by default.
- **Lessons file**: lessons recorded by agents are stored in the user state directory and injected into
  future sessions as instructions. Treat that file as trusted input only; review it if several people
  or untrusted agents share a machine.
- **Licence**: you need your own valid CATIA licence. This project does not bypass, emulate or
  circumvent licensing in any way, and will not accept contributions that do.

## Reporting a vulnerability

Please report privately through GitHub's "Report a vulnerability" (Security tab) rather than a
public issue. Include the version, what you did, and the impact. We aim to reply within a week.
