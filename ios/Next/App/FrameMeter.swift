import QuartzCore
import SwiftUI
import UIKit

/// Debug builds: measures the chat's frames while a run works (`-DotNextFrameMeter 1`), the record of the streaming
/// check (`scripts/e2e_streaming.sh`). A display link counts every frame from the run's start to its end; a frame later
/// than one and a half of the display's interval is a hitch, and what it ran over is hitch time. Beside it a probe asks
/// the main thread every 8 ms and times how long it waits (a simulator that is not on screen slows its display link,
/// the main thread's waits are measured either way). One line per run goes to `Library/Caches/dotnext-frames.txt`:
/// frames, hitches, frames over 16.7 ms, the longest frame, hitch time per second; the probe's waits over 16.7 ms and
/// over 50 ms, its longest, and the main thread's blocked time per second.
@MainActor
final class FrameMeter: NSObject {
    static let shared: FrameMeter = FrameMeter()
    /// The meter of disclosure toggles (`ToggleMeter`), apart from the run's so that the two never share a count.
    static let toggles: FrameMeter = FrameMeter()
    private var link: CADisplayLink?
    private var last: CFTimeInterval = 0
    private var started: CFTimeInterval = 0
    private var frames: Int = 0
    private var hitches: Int = 0
    private var over16: Int = 0
    private var longest: CFTimeInterval = 0
    private var hitchTime: CFTimeInterval = 0
    private var probe: Task<Void, Never>?
    private var waits: Int = 0
    private var waitsOver16: Int = 0
    private var waitsOver50: Int = 0
    private var longestWait: Double = 0
    private var blocked: Double = 0

    static var enabled: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "DotNextFrameMeter")
        #else
            false
        #endif
    }

    /// Whether disclosure toggles are measured (`-DotNextToggleMeter 1`).
    static var measuresToggles: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "DotNextToggleMeter")
        #else
            false
        #endif
    }

    func start() {
        guard link == nil else { return }
        frames = 0
        hitches = 0
        over16 = 0
        longest = 0
        hitchTime = 0
        last = 0
        waits = 0
        waitsOver16 = 0
        waitsOver50 = 0
        longestWait = 0
        blocked = 0
        started = CACurrentMediaTime()
        probe = Task.detached { [weak self] in
            while !Task.isCancelled {
                let asked: Double = CACurrentMediaTime()
                await MainActor.run { self?.waited(CACurrentMediaTime() - asked) }
                try? await Task.sleep(for: .milliseconds(8))
            }
        }
        let made: CADisplayLink = CADisplayLink(target: self, selector: #selector(frame(_:)))
        made.add(to: .main, forMode: .common)
        link = made
    }

    @objc private func frame(_ link: CADisplayLink) {
        defer { last = link.timestamp }
        guard last > 0 else { return }
        let interval: CFTimeInterval = link.timestamp - last
        let expected: CFTimeInterval = link.duration > 0 ? link.duration : 1.0 / 60.0
        frames += 1
        longest = max(longest, interval)
        if interval > 1.0 / 60.0 + 0.0005 { over16 += 1 }
        if interval > expected * 1.5 {
            hitches += 1
            hitchTime += interval - expected
        }
    }

    private func waited(_ seconds: Double) {
        waits += 1
        longestWait = max(longestWait, seconds)
        if seconds > 1.0 / 60.0 { waitsOver16 += 1 }
        if seconds > 0.05 { waitsOver50 += 1 }
        if seconds > 1.0 / 60.0 { blocked += seconds }
    }

    /// Measures one disclosure toggle (a work-log row opening or closing): the meter runs for `seconds` and its line goes
    /// out under `label` (debug builds with `-DotNextToggleMeter 1`; `scripts/jank.sh`).
    func measureToggle(label: String, seconds: Double) {
        guard link == nil else { return }
        start()
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(seconds))
            stop(label: label)
        }
    }

    func stop(label: String) {
        guard let link else { return }
        link.invalidate()
        self.link = nil
        probe?.cancel()
        probe = nil
        let seconds: Double = max(CACurrentMediaTime() - started, 0.001)
        let line: String = String(
            format: "%@ frames=%d hitches=%d over16ms=%d longest_ms=%.1f hitch_ms_per_s=%.1f | main_waits=%d over16ms=%d over50ms=%d longest_ms=%.1f blocked_ms_per_s=%.1f | seconds=%.1f\n",
            label, frames, hitches, over16, longest * 1000, hitchTime * 1000 / seconds, waits, waitsOver16, waitsOver50, longestWait * 1000,
            blocked * 1000 / seconds, seconds)
        let file: URL = URL.cachesDirectory.appending(path: "dotnext-frames.txt")
        if let handle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
            try? handle.close()
        } else {
            try? Data(line.utf8).write(to: file)
        }
    }
}

/// Starts the frame meter when a run of the open chat starts and writes its line when the run ends (debug builds with
/// `-DotNextFrameMeter 1`; nothing otherwise).
struct FrameMeterHook: ViewModifier {
    let running: Bool

    func body(content: Content) -> some View {
        if FrameMeter.enabled {
            content
                .onChange(of: running, initial: true) { _, now in
                    if now { FrameMeter.shared.start() } else { FrameMeter.shared.stop(label: "run") }
                }
                .onDisappear { FrameMeter.shared.stop(label: "run") }
        } else {
            content
        }
    }
}

/// Debug builds with `-DotNextToggleMeter 1`: measures each opening and closing of a disclosure for one second and writes
/// its line as "open" or "close" and the kind of row (`scripts/jank.sh`); nothing otherwise.
struct ToggleMeter: ViewModifier {
    let open: Bool
    /// What the row holds ("long" or "short" output), so that the lines tell the two apart.
    let kind: String

    func body(content: Content) -> some View {
        if FrameMeter.measuresToggles {
            content.onChange(of: open) { _, now in FrameMeter.toggles.measureToggle(label: (now ? "open " : "close ") + kind, seconds: 1.0) }
        } else {
            content
        }
    }
}

/// Debug builds with `-DotNextScrollMeter 1`: measures each scroll of a list, from the finger's touch until the list
/// stands still, and writes its line as "scroll <list>" (`scripts/scroll.sh`); nothing otherwise.
@MainActor
enum ScrollMeter {
    static let meter: FrameMeter = FrameMeter()

    static var enabled: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "DotNextScrollMeter")
        #else
            false
        #endif
    }

    static func phase(from old: ScrollPhase, to now: ScrollPhase, list: String) {
        guard enabled else { return }
        if old == .idle, now != .idle { meter.start() }
        if now == .idle { meter.stop(label: "scroll " + list) }
    }
}

#if DEBUG
    /// Debug builds with `-DotNextAutoScroll 1`: the chat scrolls itself, for a device whose UI automation is off
    /// (`scripts/scroll.sh` on an iPhone). Two seconds after the chat shows, its transcript (the window's tallest
    /// vertical scroll view) glides to its top and back to its end twice at 3000 points a second, the speed of a firm
    /// flick, while the frame meter measures each pass ("autoscroll up", "autoscroll down"). The rows a pass brings into
    /// sight are made as a finger's scroll makes them; only the scroll's phases (touch, glide) are not reported.
    @MainActor
    enum AutoScroller {
        static var enabled: Bool { UserDefaults.standard.bool(forKey: "DotNextAutoScroll") }
        private static var started: Bool = false

        static func start() {
            guard enabled, !started else { return }
            started = true
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2))
                guard let scroll = transcript() else { return }
                for _ in 0..<2 {
                    await glide(scroll, toTop: true)
                    await glide(scroll, toTop: false)
                }
            }
        }

        private static func transcript() -> UIScrollView? {
            let windows: [UIWindow] = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
            var found: [UIScrollView] = []
            func walk(_ view: UIView) {
                if let scroll = view as? UIScrollView, scroll.contentSize.height > scroll.bounds.height * 2 { found.append(scroll) }
                view.subviews.forEach(walk)
            }
            windows.forEach(walk)
            return found.max { $0.contentSize.height < $1.contentSize.height }
        }

        private static func glide(_ scroll: UIScrollView, toTop: Bool) async {
            let meter: FrameMeter = ScrollMeter.meter
            meter.start()
            var last: CFTimeInterval = CACurrentMediaTime()
            while true {
                try? await Task.sleep(for: .milliseconds(4))
                let now: CFTimeInterval = CACurrentMediaTime()
                let step: CGFloat = CGFloat(now - last) * 3000
                last = now
                let top: CGFloat = -scroll.adjustedContentInset.top
                let end: CGFloat = max(top, scroll.contentSize.height + scroll.adjustedContentInset.bottom - scroll.bounds.height)
                let next: CGFloat = toTop ? max(top, scroll.contentOffset.y - step) : min(end, scroll.contentOffset.y + step)
                scroll.contentOffset.y = next
                if next == (toTop ? top : end) { break }
            }
            try? await Task.sleep(for: .milliseconds(300))
            meter.stop(label: toTop ? "autoscroll up" : "autoscroll down")
        }
    }
#endif
