//! Where the window was and how big, kept across launches in the shell's own folder (`window.json` in
//! `~/Library/Application Support/dev.dot.desktop`). It is saved when the window is hidden, loses the focus, or the app
//! quits or restarts for an update, and only when it changed: moving or resizing the window writes nothing while it
//! happens, and a shell left alone writes nothing at all. A place that no display shows any more (a display taken away)
//! is not used: the window opens at its default size where macOS puts a new window.

use std::fs;
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};

/// The window's frame in logical points: the top left of its outer frame and the size of its content.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Place {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    /// Zoomed to fill the screen (the green button); the frame is then the one it had before.
    pub maximized: bool,
}

/// A display's area in logical points.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Area {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// How much of the window's top strip (its bars, which drag it) a display must show for the place to be used.
const SEEN_WIDTH: f64 = 120.0;
const SEEN_HEIGHT: f64 = 40.0;

/// The place read from `path`: `None` when there is none yet, or when what is there is not a place (it is then left
/// to be written over, and said).
pub fn read(path: &Path) -> Option<Place> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return None,
        Err(error) => {
            eprintln!(
                "dot-shell: the window's place could not be read from {}: {error}",
                path.display()
            );
            return None;
        }
    };
    match serde_json::from_slice(&bytes) {
        Ok(place) => Some(place),
        Err(error) => {
            eprintln!(
                "dot-shell: {} is not a window's place: {error}",
                path.display()
            );
            None
        }
    }
}

/// Write `place` to `path`, beside it first and renamed over it, so that a quit midway leaves the old place whole.
pub fn write(path: &Path, place: &Place) -> io::Result<()> {
    let folder = path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "the place's path has no folder",
        )
    })?;
    fs::create_dir_all(folder)?;
    let fresh = folder.join("window.json.new");
    let bytes = serde_json::to_vec(place).map_err(io::Error::other)?;
    fs::write(&fresh, bytes)?;
    fs::rename(&fresh, path)
}

/// The place to open the window at: `saved`, its size at least `min`, when one of `displays` shows enough of its top
/// strip to grab it; else `None`.
pub fn usable(saved: Place, displays: &[Area], min: (f64, f64)) -> Option<Place> {
    let finite = [saved.x, saved.y, saved.width, saved.height]
        .iter()
        .all(|value| value.is_finite());
    let seen = displays.iter().any(|area| {
        let across = (saved.x + saved.width).min(area.x + area.width) - saved.x.max(area.x);
        let down = (saved.y + SEEN_HEIGHT).min(area.y + area.height) - saved.y.max(area.y);
        across >= SEEN_WIDTH.min(saved.width) && down >= SEEN_HEIGHT
    });
    (finite && seen).then_some(Place {
        width: saved.width.max(min.0),
        height: saved.height.max(min.1),
        ..saved
    })
}

/// The place to save now: the window's `frame` when it is not zoomed; zoomed, the frame it had before (`last`) with
/// the zoom, since macOS gives no other.
pub fn to_save(frame: Place, maximized: bool, last: Option<Place>) -> Place {
    match (maximized, last) {
        (true, Some(before)) => Place {
            maximized: true,
            ..before
        },
        _ => Place { maximized, ..frame },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    const LAPTOP: Area = Area {
        x: 0.0,
        y: 0.0,
        width: 1512.0,
        height: 982.0,
    };
    const MIN: (f64, f64) = (390.0, 600.0);

    fn place(x: f64, y: f64) -> Place {
        Place {
            x,
            y,
            width: 1280.0,
            height: 860.0,
            maximized: false,
        }
    }

    #[test]
    fn a_place_a_display_shows_is_used_and_one_none_shows_is_not() {
        assert_eq!(
            usable(place(100.0, 60.0), &[LAPTOP], MIN),
            Some(place(100.0, 60.0))
        );
        // On a display to the right that was taken away.
        assert_eq!(usable(place(1800.0, 60.0), &[LAPTOP], MIN), None);
        let right = Area {
            x: 1512.0,
            y: -200.0,
            width: 2560.0,
            height: 1440.0,
        };
        assert_eq!(
            usable(place(1800.0, 60.0), &[LAPTOP, right], MIN),
            Some(place(1800.0, 60.0))
        );
        // Its bars above the top of every display, or only a sliver of it at an edge.
        assert_eq!(usable(place(100.0, -500.0), &[LAPTOP], MIN), None);
        assert_eq!(usable(place(1450.0, 60.0), &[LAPTOP], MIN), None);
        assert_eq!(usable(place(f64::NAN, 60.0), &[LAPTOP], MIN), None);
        // A size under the window's least is raised to it.
        let small = Place {
            width: 200.0,
            height: 100.0,
            ..place(100.0, 60.0)
        };
        assert_eq!(
            usable(small, &[LAPTOP], MIN).map(|used| (used.width, used.height)),
            Some(MIN)
        );
    }

    #[test]
    fn a_zoomed_window_keeps_the_frame_it_had_before() {
        let before = place(100.0, 60.0);
        let zoomed = Place {
            x: 0.0,
            y: 0.0,
            width: 1512.0,
            height: 950.0,
            maximized: false,
        };
        assert_eq!(
            to_save(zoomed, true, Some(before)),
            Place {
                maximized: true,
                ..before
            }
        );
        assert_eq!(
            to_save(zoomed, true, None),
            Place {
                maximized: true,
                ..zoomed
            }
        );
        assert_eq!(to_save(before, false, Some(zoomed)), before);
    }

    #[test]
    fn a_place_is_written_whole_and_read_back() {
        let folder = env::temp_dir().join(format!("dot-shell-place-{}", std::process::id()));
        let _ = fs::remove_dir_all(&folder);
        let path = folder.join("window.json");
        assert_eq!(read(&path), None);
        write(&path, &place(100.0, 60.0)).expect("written");
        assert_eq!(read(&path), Some(place(100.0, 60.0)));
        fs::write(&path, b"{\"x\": 1}").expect("a broken place");
        assert_eq!(read(&path), None);
        let _ = fs::remove_dir_all(&folder);
    }
}
