import DotEngine
import Foundation
import Observation
import UIKit
import os

// Taking a run's page over from the phone (item 1840), as the web's "Use it here" (`computer.js`, `control.js`): the run
// is held at its next step without anything coming to the front of the Mac (`/takeover/screen`), the owner's taps,
// drags, presses and keys go to the page (`/input`, in order, one batch at a time), and Hand back lets the run go on.
// What the daemon refuses is said in its own words: `[browser] remote_input` off, a step still going, a run given back
// elsewhere.

/// The owner's hold of one run's page from this phone.
@MainActor
@Observable
final class RunControl: CanvasControl {
    enum Phase: Equatable {
        case watching
        case taking
        case held
        case givingBack
    }

    let run: String
    private(set) var phase: Phase = .watching
    /// The line under the picture while the phone holds the run: that taps go to the page, or that a step still ends.
    private(set) var note: String?
    static let heldNote: String = String(localized: "Your taps go to the page.", comment: "Takeover: the line under the picture once the phone holds the run (web computer.js)")
    static let busyNote: String = String(
        localized: "Dot is finishing a step; try again in a moment.", comment: "Takeover: the page is busy with the run's last step (web computer.js)")
    /// Why the daemon refused the takeover or the input, in its words.
    private(set) var refusal: String?
    /// Bumped on each change the owner should feel (taken, given back).
    private(set) var feel: Int = 0
    @ObservationIgnored private var api: DaemonAPI?
    @ObservationIgnored private var queue: [RemoteInputEvent] = []
    @ObservationIgnored private var sending: Bool = false
    @ObservationIgnored private var flushTask: Task<Void, Never>?
    /// The page's width in CSS pixels, from the daemon's last answer; the picture's own until then.
    @ObservationIgnored var pageWidth: Double = 1280
    @ObservationIgnored private var pressed: PictureFraction?
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "takeover")

    init(run: String) {
        self.run = run
    }

    /// Whether the owner's touches go to the page now.
    var controlling: Bool { phase == .held }

    /// Follows the run's own record: a hold that ended elsewhere (the web, the Mac, the run's end) ends it here, and a
    /// hold made on another screen is one this phone can use.
    func follow(_ hold: RunHold, active: Bool) {
        switch (hold, phase) {
        case (.free, .held), (.free, .givingBack):
            phase = .watching
            queue.removeAll()
        case (.held, .watching):
            phase = .held
        default:
            break
        }
        if !active, phase != .watching {
            phase = .watching
            queue.removeAll()
        }
    }

    func takeOver(api: DaemonAPI) async {
        guard phase == .watching else { return }
        self.api = api
        phase = .taking
        refusal = nil
        do {
            try await api.takeOverOnScreen(run: run)
            phase = .held
            note = Self.heldNote
            feel += 1
        } catch {
            phase = .watching
            refusal = refusalWords(error)
            logger.warning("takeover refused run=\(self.run, privacy: .public) status=\(error.statusCode ?? 0, privacy: .public)")
        }
    }

    func handBack(api: DaemonAPI) async {
        guard phase == .held else { return }
        phase = .givingBack
        do {
            try await api.handBack(run: run)
            phase = .watching
            note = nil
            feel += 1
        } catch {
            // Given back elsewhere already: the run goes on either way.
            phase = error.statusCode == 409 ? .watching : .held
            refusal = error.statusCode == 409 ? nil : refusalWords(error)
        }
        queue.removeAll()
    }

    func use(api: DaemonAPI) { self.api = api }

    func clearRefusal() { refusal = nil }

    // MARK: The canvas's touches

    func tap(at place: PictureFraction, clicks: Int) {
        send(tapEvents(at: place, button: .left, clicks: clicks), now: true)
    }

    func scroll(by points: CGVector, at place: PictureFraction, drawnWidth: CGFloat) {
        let wheel = scrollFor(dx: Double(points.dx), dy: Double(points.dy), drawnWidth: Double(drawnWidth), pageWidth: pageWidth)
        guard wheel.dx != 0 || wheel.dy != 0 else { return }
        send([.wheel(place, dx: wheel.dx, dy: wheel.dy)], now: false)
    }

    func press(_ state: UIGestureRecognizer.State, at place: PictureFraction) {
        switch state {
        case .began:
            pressed = place
            send([.move(place), .down(place, button: .left, clicks: 1)], now: true)
        case .changed:
            pressed = place
            send([.move(place)], now: false)
        case .ended, .cancelled, .failed:
            // Never left down: let go where the finger was last.
            let at: PictureFraction = pressed ?? place
            pressed = nil
            send([.up(at, button: .left, clicks: 1)], now: true)
        default:
            break
        }
    }

    /// Text typed on the phone's keyboard, a key (Return, Backspace).
    func type(_ text: String) { send(textEvents(text), now: false) }
    func key(_ name: String) { send([.key(name)], now: true) }

    // MARK: Sending

    private func send(_ events: [RemoteInputEvent], now: Bool) {
        guard phase == .held, !events.isEmpty else { return }
        queue += events
        if now {
            flush()
        } else if flushTask == nil {
            // Moves, scrolls and text wait 50 ms to go with the next, as the web's do.
            flushTask = Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(50))
                self?.flushTask = nil
                self?.flush()
            }
        }
    }

    private func flush() {
        guard !sending, !queue.isEmpty, let api else { return }
        sending = true
        let batch: [RemoteInputEvent] = Array(queue.prefix(remoteBatchLimit))
        queue.removeFirst(batch.count)
        let run: String = run
        Task { [weak self] in
            do throws(DaemonRequestError) {
                let taken: RemoteInputResult = try await api.sendInput(run: run, events: batch)
                self?.pageWidth = Double(max(taken.width, 1))
                // A batch the page took ends a "finishing a step" line: the page is the owner's again.
                if self?.note == RunControl.busyNote { self?.note = RunControl.heldNote }
            } catch {
                self?.refused(error)
            }
            self?.sending = false
            self?.flush()
        }
    }

    private func refused(_ error: DaemonRequestError) {
        queue.removeAll()
        if error.statusCode == 409, error.detail.contains("still going") {
            note = Self.busyNote
            return
        }
        if error.statusCode == 409, error.detail.contains("not taken over") {
            phase = .watching
            note = nil
            return
        }
        refusal = refusalWords(error)
    }

    private func refusalWords(_ error: DaemonRequestError) -> String {
        if error.detail.contains("remote_input") {
            return String(
                localized: "Using the page from the phone is off on your Mac: Settings › Browser › Use the page from the screen while I hold the task.",
                comment: "Takeover refused: [browser] remote_input is off on the Mac")
        }
        if error.refusedToAPhone {
            return String(localized: "Your Mac does not let a phone do this.", comment: "Takeover refused to a phone's key")
        }
        return error.detail.isEmpty ? feedReason(error) : error.detail
    }
}
