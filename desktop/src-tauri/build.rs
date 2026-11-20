// The commands of the installer's page (item 575) are named here, in the app manifest: Tauri then lets no page call them
// without a permission of its own (`allow-installer-inspect` and the others), for a local page as for a remote one. Only
// capabilities/installer.json grants them, to the window `installer` and to local pages. src/installer.rs keeps this
// list, the handler of src/lib.rs and that capability equal (a test reads all three).
//
// The tools the app carries are named one by one in tauri.conf.json (`bundle.resources`), never as a glob, so that
// whatever else lies in resources/tools does not ship. tauri-build wants each of them to exist, and a build that has
// not fetched them (a development build, the tests) gets an empty stand-in here: it is no program (it cannot be run),
// so the shell sees no tools and shows no installer. desktop/fetch-tools.sh puts the real ones in their place.
//
// The requirements file and the wheel (resources/requirements.txt and
// resources/dotagent_macos-<version>-py3-none-any.whl, which desktop/release.sh makes from uv.lock) are named the
// same way and have empty stand-ins too: the installer takes an empty requirements file for no hashes.
const TOOLS: [&str; 3] = ["uv", "rg", "dot-launcher"];
const REQUIREMENTS: &str = "requirements.txt";

fn main() {
    let resources = std::path::Path::new("resources");
    let requirements = resources.join(REQUIREMENTS);
    if requirements.symlink_metadata().is_err() {
        std::fs::write(&requirements, b"")
            .expect("an empty stand-in for the requirements this build does not carry");
    }
    let version = std::env::var("CARGO_PKG_VERSION").expect("the package version");
    let wheel = resources.join(format!("dotagent_macos-{version}-py3-none-any.whl"));
    if wheel.symlink_metadata().is_err() {
        std::fs::write(&wheel, b"")
            .expect("an empty stand-in for the wheel this build does not carry");
    }
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
