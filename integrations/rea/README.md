# REA for Dot

[REA (Reverse Engineer Anything)](https://github.com/morluto/rea) inspects what an app really does from outside: native
binaries, macOS and Android packages, JavaScript and Electron bundles, websites and recorded network captures, with
evidence and its limits. This folder is a Dot plugin: it registers REA's MCP server and installs REA's own workflow
skill, so a run can understand a feature of a shipped app and rebuild it.

## Install

Copy the folder into Dot's plugins folder:

```bash
cp -R integrations/rea "$HOME/Library/Application Support/Dot/plugins/rea"
```

or name it in `config.toml`, so the checkout stays the copy Dot reads:

```toml
[plugins]
dirs = ["/absolute/path/to/Dot/integrations/rea"]
```

Dot reads plugins when it starts: restart it (`dotx service restart`, or quit and open Dot.app). Requirements: Node.js
22.19+, 24.11+ or 26+ with npm, from which `npx` fetches `rea-agents` 6.5.0, the release `.mcp.json` names, and for
native analysis one of Hopper, Ghidra or IDA. Static JavaScript, Electron, .NET, plist and artifact analysis need no
engine.

## What Dot gets

- The server `rea-mcp`, whose tools a run calls as `mcp__rea-mcp__<tool>`: the 30 of REA's 207 that cover a whole
  investigation — `open_binary`, `inspect_macho`, `inspect_signature`, `inspect_plist`, `list_architectures`,
  `procedure_pseudo_code`, `analyze_function`, `batch_decompile`, `search_strings`, `xrefs`, `trace_feature`,
  `trace_call_path`, the browser and JavaScript tools, the Android ones and `export_evidence_bundle`. The other tools
  stay on REA's command line (`npx -y rea-agents@6.5.0 --help`), which a shell uses.
- The browser tools (`list_browser_targets`, `inspect_web_page`, `observe_javascript_runtime`,
  `capture_web_screenshot`) attach to the Chrome DevTools endpoint on this Mac that a call names, Dot's own Chrome's
  too, where your sign-ins are. REA leaves credential headers and stored values out of what it captures; the text of a
  page and a screenshot of it are kept. A `yolo` run uses them without asking, as it does every tool.
- The skill `reverse-engineer-anything` from REA's package, unchanged: when REA is the evidence a claim needs, when
  reading the source is enough, and how to keep the Evidence and its limitations in the answer. It shows under
  Customize › Skills as a system skill of the plugin.
- A 300 s start, for `npx` and an analysis engine, and a 600 s call, for a long analysis. `allow`, `startup_timeout_s`
  and `call_timeout_s` are Dot's own keys in a plugin's `.mcp.json`; they change nothing but what Dot does with the
  server.

## Verify

```bash
dotx skills list | grep -i reverse-engineer    # the skill, from plugin rea
npx -y rea-agents@6.5.0 doctor                 # REA's own readiness check, per provider
```

In a run, `mcp__rea-mcp__binary_session` reports the target and the provider, and `open_binary` takes an absolute path.

## Update

The server runs the release `.mcp.json` names, not the newest one npm has: a new release of REA runs with your rights
only once you name it. To update, read what the release changes, name it in `.mcp.json` and as `version` in
`.claude-plugin/plugin.json`, copy `skills/reverse-engineer-anything` from that release's package over this folder's,
and install the folder again. If a release renames a tool, `dotx mcp list` names the one the server no longer offers
and `allow` takes the new name. The skill's own steps that register REA in other clients (`setup --client …`) are not
for Dot, where this plugin registers the server.

## Licence

REA is MIT, Copyright (c) 2026 morluto; the licence is this folder's [LICENSE](LICENSE). The skill under `skills/` is
REA's, unchanged.
