# Deep-OS: the command before the window

Dot's prompt already prefers a command to a click. This page is the map that makes the choice quick: for a Mac
setting or an app command, the line of shell or AppleScript behind it, and how to check that it took.

The same map ships as the built-in skill `deep-os` (`src/dotagent/skills/builtin/deep-os/SKILL.md`), so a run finds
it when the task is a Mac setting or an app command. This page is for the owner and for a change to the map.

## Why

- One command comes back with its answer, in one call, and does not depend on a window's place or a screen's size.
- A click through System Settings is many model round trips and a picture to read; `networksetup -setairportpower`
  is one call and a line to read back.
- A command is repeatable and can be verified; a click is a guess about what the UI did.

## The map

| What | The fast path |
| --- | --- |
| Wi-Fi off / on | `networksetup -listallhardwareports` finds the Wi-Fi device, then `networksetup -setairportpower en0 off` / `on`; read with `-getairportpower en0`. |
| Wi-Fi service off | `networksetup -setnetworkserviceenabled "Wi-Fi" off` (the service, not the radio). |
| Volume | `osascript -e 'set volume output volume 40'`; mute with `set volume with output muted`. |
| Dark mode | `osascript -e 'tell application "System Events" to tell appearance preferences to set dark mode to true'`. |
| Screen off | `pmset displaysleepnow`. |
| Keep awake | `caffeinate -dimsu -t 3600` in a terminal session. |
| Screenshot | `screencapture -x /tmp/shot.png`; `desktop_screenshot` for the conversation. |
| Clipboard | `pbcopy` / `pbpaste`; the `desktop_clipboard_*` tools for the owner's clipboard. |
| Notification / speech | `osascript -e 'display notification ...'`; `say "text"`. |
| Ports / processes | `lsof -nP -iTCP -sTCP:LISTEN`; `pgrep -lf <name>` (`ps` and `top` do not start in the sandbox). |
| Disk / battery | `df -h`, `du -sh <path>`, `pmset -g batt`. |
| Finder / Music | AppleScript: `tell application "Finder" to ...`, `tell application "Music" to playpause`. |
| Mail / calendars | `mail_search`, `mail_read`, `calendar_events` before AppleScript. |
| Dot | `dotx service status`; `dotx service restart --when-idle`, never twice in a row. |

The skill carries the longer table, the destructive commands that wait for the owner's word, and what to do when a
command fails (a missing tool, an Automation prompt, a `sudo` prompt in `terminal_open`).

## What it is not

It is not a new tool and not a new permission: `shell` and `apple_script` already run these, and the policy that
judges a shell command still judges it. It is a habit and a map, so the run reaches for the line before the window.
