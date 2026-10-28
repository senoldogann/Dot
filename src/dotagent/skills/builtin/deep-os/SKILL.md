---
name: deep-os
description: "Do a Mac task by the command that does it instead of clicking through its windows: Wi-Fi, sound, appearance, power, screenshots, network, processes, Finder and Dot itself. Use when a task is a Mac setting, a system action or an app command, and before driving the UI."
---

# Deep-OS: the command before the window

A Mac setting or an app command usually has a line of shell or AppleScript behind it. It is one call, it comes back
with its answer, and it does not depend on where a window is. Reach for the UI only when no command does the job.

## How to run one

- `shell` for a command line (`networksetup`, `pmset`, `defaults`, `pbcopy`, `lsof`, `pgrep`, `date`).
- `apple_script` for a scriptable app (Finder, Music, Mail, Calendar, System Events).
- `terminal_open` when the command asks a question, needs `sudo`, or must stay up (`caffeinate`).
- Read the setting back after writing it (`networksetup -getairportpower`, `pmset -g`, `osascript` again). A command
  that printed nothing has not proved it did the thing.
- `open` and `launchctl` do not run in the sandbox: use `desktop_open_app` for an app, and `dotx` for Dot.

## The map

| What the owner asks | The fast path |
| --- | --- |
| Wi-Fi off / on | `networksetup -listallhardwareports` finds the Wi-Fi device (`en0` usually), then `networksetup -setairportpower en0 off` / `on`. Read it with `-getairportpower en0`. |
| Wi-Fi service off (not the radio) | `networksetup -setnetworkserviceenabled "Wi-Fi" off` / `on`; this is the service in Network settings, not the antenna. |
| Which Wi-Fi | `networksetup -getairportnetwork en0`; the whole service with `networksetup -getinfo "Wi-Fi"`. |
| Volume to 40 % | `osascript -e 'set volume output volume 40'`; read with `osascript -e 'output volume of (get volume settings)'`. |
| Mute / unmute | `osascript -e 'set volume with output muted'` / `set volume without output muted`. |
| Dark / light mode | `osascript -e 'tell application "System Events" to tell appearance preferences to set dark mode to true'` (and `false`). |
| Screen off now | `pmset displaysleepnow`; it locks when the Mac asks for a password after sleep. |
| Keep awake an hour | `caffeinate -dimsu -t 3600` in a terminal session; end it with `terminal_close`. |
| Screenshot | `screencapture -x /tmp/shot.png`; `desktop_screenshot` when the picture is for the conversation. |
| Clipboard | `pbcopy` / `pbpaste`; `desktop_clipboard_write` / `desktop_clipboard_read` when it is the owner's clipboard. |
| Notification | `osascript -e 'display notification "text" with title "title"'`. |
| Speak | `say "text"`. |
| Open an app | `desktop_open_app`; in a terminal `open -a "App"`. |
| Lock now | `osascript -e 'tell application "System Events" to key code 12 using {control down, command down}'`. |
| Bluetooth off / on | `blueutil -p 0` / `-p 1` when it is installed; `system_profiler SPBluetoothDataType` reads it. |
| Network addresses | `ifconfig`, `route -n get default`, `scutil --dns`; the public address with `curl -s https://ifconfig.me`. |
| Ports in use | `lsof -nP -iTCP -sTCP:LISTEN`. |
| Processes | `pgrep -lf <name>`; `ps` and `top` do not start in the sandbox. |
| Disk | `df -h`; a folder with `du -sh <path>`. |
| Battery | `pmset -g batt`. |
| Time / time zone | `date`; `systemsetup -gettimezone` reads the zone, setting it needs `sudo`. |
| Finder | `osascript -e 'tell application "Finder" to ...'` (empty the trash, reveal a file, get the selection). |
| Music | `osascript -e 'tell application "Music" to playpause'`; `next track`, `previous track`. |
| Mail and calendars | `mail_search`, `mail_read`, `calendar_events` before AppleScript. |
| Dot itself | `dotx service status`; restart with `dotx service restart --when-idle` (never twice in a row), and only when idle. |

## Before a destructive command

Emptying the trash, `killall`, `shutdown`, `restart`, erasing a disk and changing a login item are irreversible or
close to it. Say what the command will do and wait for the owner's word; a setting that can be put back
(`-setairportpower on`) is not one of these.

## When the command fails

Read the error: a missing tool is installed with `brew` (ask first), a permission error is a macOS Automation or
Accessibility prompt the owner must answer, and a `sudo` prompt belongs in `terminal_open`. Do not fall back to
clicking through a window before trying the line the error points to.
