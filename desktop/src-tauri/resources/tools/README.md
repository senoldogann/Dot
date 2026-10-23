The tools the desktop app carries (item 575) go here before the app is built: `uv`, `rg` and `dot-launcher`, each a
universal program. `desktop/fetch-tools.sh` fetches the pinned `uv` and `rg` (checked against the SHA-256s of
`desktop/tools.lock`), builds the launcher from `src/dotagent/dot_app.c`, and `desktop/release.sh` signs the three with
the Developer ID before the app is signed. Git keeps none of them. A build without them (`npm run dev`, a plain
`npm run build`) has no installer: the window shows what it always showed.
