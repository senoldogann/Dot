---
name: onedrive
description: Find, read or change the files the user keeps in OneDrive on this Mac, where its synced folders are, files kept only online, and what a change there does to the copy in the cloud.
---

# OneDrive on this Mac

## Where the files are

- OneDrive keeps each account's files in a folder of `~/Library/CloudStorage`: `OneDrive-Personal` for a personal
  Microsoft account, `OneDrive-` and the organization's name for a work or school account, and
  `OneDrive-SharedLibraries-` and that name for the organization's libraries the user syncs. `ls ~/Library/CloudStorage`
  lists them; Finder shows them under Locations.
- When the user says "my OneDrive" and there are two such folders, ask which one, unless the path or the files make it
  clear.
- No such folder means OneDrive syncs nothing on this Mac: the OneDrive app is not installed or not signed in. Say so;
  the user signs in in the OneDrive app.

## Files kept only online

- The folder lists every file, but OneDrive may keep the contents of a file only online until something opens it.
  `ls -lO` marks such a file `dataless`; the size it shows is the size online.
- Reading one downloads it first, which needs the OneDrive app running (`pgrep -x OneDrive` finds it) and the Mac
  online; without them the read waits or fails. When the app is not running, start it with `open -a OneDrive`, or ask
  the user to, and try again.
- Look for files by their names (`ls`, `find -name`) and read only the ones you need: searching the contents of the
  whole folder downloads every file in it.

## Changes reach the cloud

- What you change, rename, move or delete in these folders changes on OneDrive itself, on the user's other devices and
  for everyone the file is shared with. A deleted file goes to the OneDrive recycle bin online.
- Change a file there only when the user asked for that change, and write new files elsewhere (the workspace,
  Documents) unless the user asked for them in OneDrive.
- An Office file open in Excel or Word saves to OneDrive by itself: change it through the application, or ask the user
  to close it first.
