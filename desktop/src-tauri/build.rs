// The commands of the installer's page (item 575) are named here, in the app manifest: Tauri then lets no page call them
// without a permission of its own (`allow-installer-inspect` and the others), for a local page as for a remote one. Only
// capabilities/installer.json grants them, to the window `installer` and to local pages. src/installer.rs keeps this
// list, the handler of src/lib.rs and that capability equal (a test reads all three).
//
// The tools the app carries are named one by one in tauri.conf.json (`bundle.resources`), never as a glob, so that
// whatever else lies in resources/tools does not ship. tauri-build wants each of them to exist, and a build that has
// not fetched them (a development build, the tests) gets an empty stand-in here: it is no program (it cannot be run),
// so the shell sees no tools and shows no installer. desktop/fetch-tools.sh puts the real ones in their place.
const TOOLS: [&str; 3] = ["uv", "rg", "dot-launcher"];

fn main() {
    for name in TOOLS {
        let path = std::path::Path::new("resources").join("tools").join(name);
        if path.symlink_metadata().is_err() {
            std::fs::write(&path, b"")
                .expect("an empty stand-in for a tool this build does not carry");
        }
    }
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "installer_inspect",
            "installer_status",
            "installer_start",
            "installer_retry",
            "installer_open",
        ]),
    ))
    .expect("the build script of the desktop shell failed");
}
