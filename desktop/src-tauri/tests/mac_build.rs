//! What the Mac build, the only one Dot makes, does not carry (item 766): the crates whose advisories
//! `.cargo/audit.toml` ignores. `cargo audit` reads the whole lock file, every target's crates in it, so a crate that
//! only a Windows build pulls fails the audit of a build that never has it; the ignore stands as long as this test
//! holds, and the day one of those crates reaches the Mac build, the test fails and the ignore goes.

use std::process::Command;

/// The crates `.cargo/audit.toml` ignores advisories of, which must stay out of the Mac build.
const IGNORED: &[&str] = &["zip"];
const MAC_TARGETS: &[&str] = &["aarch64-apple-darwin", "x86_64-apple-darwin"];

#[test]
fn the_crates_whose_advisories_are_ignored_are_not_in_the_mac_build() {
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".to_string());
    for krate in IGNORED {
        for target in MAC_TARGETS {
            let output = Command::new(&cargo)
                .args([
                    "tree",
                    "--invert",
                    krate,
                    "--target",
                    target,
                    "--edges",
                    "normal,build",
                    "--locked",
                ])
                .current_dir(env!("CARGO_MANIFEST_DIR"))
                .output()
                .expect("cargo tree runs");
            // A crate in the lock file that no Mac target pulls prints nothing (a warning says so); one that is not
            // in the lock file at all fails the command, and then the ignore is stale too.
            assert!(
                output.status.success(),
                "cargo tree failed: is {krate} still in Cargo.lock? If not, its advisories go from .cargo/audit.toml\n{}",
                String::from_utf8_lossy(&output.stderr)
            );
            let listed = String::from_utf8_lossy(&output.stdout);
            assert!(
                !listed.contains(&format!("{krate} v")),
                "{krate} reached the {target} build: take its advisories out of .cargo/audit.toml and update it\n{listed}"
            );
        }
    }
}
