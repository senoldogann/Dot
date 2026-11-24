import Foundation

/// The icons the phone draws (item 1461): Lucide's shapes, the web app's own set (`static/js/icons.js`), as the images of
/// `Dot/Resources/Icons.xcassets` that `DotIcon` shows. A case is the name of its image, and a screen can only draw a case:
/// a name that is not here does not compile, so none can be drawn from a catalog that lacks it. A new icon is a new line
/// here (one case a line, in order) and `uv run python scripts/export_ios_icons.py`, which writes its image from icons.js
/// (or from the Lucide shapes the script keeps for what the web app does not draw yet); `tests/unit/test_ios_icons.py`
/// fails until the catalog is that. SF Symbols stay only where the system's own meaning matters (a notification action,
/// the share sheet, the keyboard).
enum IconName: String, CaseIterable, Sendable {
    case alert
    case arrowDown
    case arrowUp
    case arrowUpToLine
    case audioWaveform
    case badgeCheck
    case bell
    case bot
    case brain
    case branch
    case calendar
    case calendarClock
    case check
    case chevron
    case chevronDown
    case chevronsUpDown
    case chevronUp
    case circleAlert
    case circleCheck
    case circleDashed
    case circleMinus
    case circlePause
    case circleX
    case clock
    case copy
    case cornerDownRight
    case cornerUpRight
    case cpu
    case database
    case external
    case eye
    case file
    case globe
    case hand
    case help
    case history
    case inbox
    case info
    case key
    case laptop
    case layers
    case list
    case listOrdered
    case listPlus
    case lock
    case loop
    case mail
    case mailOpen
    case message
    case mic
    case monitor
    case monitorOff
    case network
    case pencil
    case plug
    case plus
    case puzzle
    case refresh
    case search
    case send
    case settings
    case shieldCheck
    case sliders
    case smilePlus
    case sparkles
    case squarePen
    case stop
    case terminal
    case textCursor
    case trash
    case users
    case wifiOff
    case wrench
    case x
}
