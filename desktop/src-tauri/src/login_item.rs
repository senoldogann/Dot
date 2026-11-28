//! The shell's login item (`[desktop_app] start_at_login`): a launchd job of the owner's, in
//! `~/Library/LaunchAgents`, that opens the app at each login with `--at-login`, so that it starts in the menu bar with
//! its window hidden. launchd reads the folder at login, so writing the file starts nothing now; removing it takes the
//! login item away. The job opens the app through LaunchServices (`/usr/bin/open`), as a click in the Finder does, so
//! the app runs as itself and not as a child of launchd.
//!
//! Only an app run from its bundle has a login item: a build run from `target/` (development, the tests), an app macOS
//! runs from a translocated copy, or one run from a disk image would name a place that is gone at the next login.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// The job's label, the app's identifier (`tauri.conf.json`).
pub const LABEL: &str = "dev.dot.desktop";
/// The argument the job opens the app with: the window stays hidden.
pub const AT_LOGIN: &str = "--at-login";

/// The bundle (`…/Dot.app`) the program at `exe` runs from, when it is one that stays where it is: the program is
/// `Dot.app/Contents/MacOS/<name>`, not in a translocated copy (`/AppTranslocation/`) nor on a mounted disk image
/// (`/Volumes/`).
pub fn bundle_of(exe: &Path) -> Option<PathBuf> {
    crate::installer::app_of(exe).filter(|bundle| {
        exe.parent().and_then(Path::file_name) == Some("MacOS".as_ref())
            && bundle.to_str().is_some_and(|text| {
                !text.contains("/AppTranslocation/") && !text.starts_with("/Volumes/")
            })
    })
}

/// The job's property list for the app at `bundle`, as launchd reads it: run once as the owner logs in to the window
/// session, the app opened in the background with `AT_LOGIN`.
pub fn job(bundle: &Path) -> Vec<u8> {
    let mut dict = plist::Dictionary::new();
    dict.insert("Label".to_string(), plist::Value::String(LABEL.to_string()));
    dict.insert(
        "ProgramArguments".to_string(),
        plist::Value::Array(
            [
                "/usr/bin/open",
                "-g",
                "-a",
                &bundle.to_string_lossy(),
                "--args",
                AT_LOGIN,
            ]
            .iter()
            .map(|part| plist::Value::String(part.to_string()))
            .collect(),
        ),
    );
    dict.insert("RunAtLoad".to_string(), plist::Value::Boolean(true));
    dict.insert(
        "LimitLoadToSessionType".to_string(),
        plist::Value::String("Aqua".to_string()),
    );
    dict.insert(
        "ProcessType".to_string(),
        plist::Value::String("Interactive".to_string()),
    );
    let mut bytes = Vec::new();
    plist::Value::Dictionary(dict)
        .to_writer_xml(&mut bytes)
        .expect("a dictionary of strings is written as XML");
    bytes
}

/// Where the job lives for the owner whose home is `home`.
pub fn job_path(home: &Path) -> PathBuf {
    home.join("Library")
        .join("LaunchAgents")
        .join(format!("{LABEL}.plist"))
}

/// What a change of the login item did.
#[derive(Debug, PartialEq, Eq)]
pub enum Done {
    Written,
    Removed,
    AsItWas,
}

/// Make the job at `path` be `wanted` (`Some`: these bytes, `None`: no job). A file that already holds them is left as
/// it is; a new one is written beside it and renamed over it, so that a link at the name is replaced, not followed.
pub fn apply(path: &Path, wanted: Option<&[u8]>) -> io::Result<Done> {
    let held = match fs::read(path) {
        Ok(bytes) => Some(bytes),
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    match (wanted, held) {
        (None, None) => Ok(Done::AsItWas),
        (None, Some(_)) => fs::remove_file(path).map(|()| Done::Removed),
        (Some(bytes), Some(held)) if held == bytes => Ok(Done::AsItWas),
        (Some(bytes), _) => {
            let folder = path.parent().ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidInput, "the job's path has no folder")
            })?;
            fs::create_dir_all(folder)?;
            let fresh = folder.join(format!(".{LABEL}.plist.new"));
            let mut file = fs::File::create(&fresh)?;
            file.write_all(bytes)?;
            file.sync_all()?;
            fs::rename(&fresh, path)?;
            Ok(Done::Written)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn only_an_app_run_from_a_bundle_that_stays_has_a_login_item() {
        let bundle = |exe: &str| bundle_of(Path::new(exe));
        assert_eq!(
            bundle("/Applications/Dot.app/Contents/MacOS/dot-desktop"),
            Some(PathBuf::from("/Applications/Dot.app"))
        );
        assert_eq!(
            bundle("/Users/someone/Applications/Dot.app/Contents/MacOS/dot-desktop"),
            Some(PathBuf::from("/Users/someone/Applications/Dot.app"))
        );
        for exe in [
            "/Users/someone/Dot/desktop/src-tauri/target/debug/dot-desktop",
            "/private/var/folders/x/AppTranslocation/1234/d/Dot.app/Contents/MacOS/dot-desktop",
            "/Volumes/Dot/Dot.app/Contents/MacOS/dot-desktop",
            "/Applications/Dot/Contents/MacOS/dot-desktop",
            "dot-desktop",
        ] {
            assert_eq!(bundle(exe), None, "{exe}");
        }
    }

    #[test]
    fn the_job_opens_the_bundle_hidden_at_login_and_is_written_or_removed_only_on_a_change() {
        let bytes = job(Path::new("/Applications/Dot.app"));
        let Ok(plist::Value::Dictionary(read)) =
            plist::Value::from_reader(std::io::Cursor::new(&bytes))
        else {
            panic!("the job is a property list");
        };
        assert_eq!(read["Label"].as_string(), Some(LABEL));
        let arguments: Vec<&str> = read["ProgramArguments"]
            .as_array()
            .expect("the program's arguments")
            .iter()
            .filter_map(plist::Value::as_string)
            .collect();
        assert_eq!(
            arguments,
            [
                "/usr/bin/open",
                "-g",
                "-a",
                "/Applications/Dot.app",
                "--args",
                AT_LOGIN
            ]
        );
        assert_eq!(read["RunAtLoad"].as_boolean(), Some(true));

        let home = env::temp_dir().join(format!("dot-shell-login-{}", std::process::id()));
        let _ = fs::remove_dir_all(&home);
        let path = job_path(&home);
        assert_eq!(
            apply(&path, None).expect("nothing to remove"),
            Done::AsItWas
        );
        assert_eq!(apply(&path, Some(&bytes)).expect("written"), Done::Written);
        assert_eq!(fs::read(&path).expect("the job"), bytes);
        assert_eq!(
            apply(&path, Some(&bytes)).expect("unchanged"),
            Done::AsItWas
        );
        let moved = job(Path::new("/Users/someone/Applications/Dot.app"));
        assert_eq!(
            apply(&path, Some(&moved)).expect("rewritten"),
            Done::Written
        );
        assert_eq!(apply(&path, None).expect("removed"), Done::Removed);
        assert!(!path.exists());
        let leftovers: Vec<_> = fs::read_dir(path.parent().expect("the folder"))
            .expect("the folder")
            .collect();
        assert!(leftovers.is_empty(), "{leftovers:?}");
        let _ = fs::remove_dir_all(&home);
    }
}
