import DotEngine
import UIKit
import XCTest

@testable import DotNext

/// Item 1926: the pure rules of what the app costs and of its performance record.
final class PerformanceTests: XCTestCase {
    func testAPictureIsCutToItsLongSideAndASmallOneIsKept() {
        XCTAssertEqual(fittedSize(CGSize(width: 4032, height: 3024), longest: 1600), CGSize(width: 1600, height: 1200))
        XCTAssertEqual(fittedSize(CGSize(width: 3024, height: 4032), longest: 1600), CGSize(width: 1200, height: 1600))
        XCTAssertEqual(fittedSize(CGSize(width: 800, height: 600), longest: 1600), CGSize(width: 800, height: 600))
        XCTAssertEqual(fittedSize(CGSize(width: 10000, height: 10), longest: 1600), CGSize(width: 1600, height: 2))
    }

    func testACameraPhotoGoesAtTheUploadSize() throws {
        let format: UIGraphicsImageRendererFormat = UIGraphicsImageRendererFormat()
        format.scale = 1
        let photo: UIImage = UIGraphicsImageRenderer(size: CGSize(width: 4032, height: 3024), format: format).image { context in
            UIColor.gray.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 4032, height: 3024))
        }
        let sized: UIImage = ImageSizing.fitted(photo, longest: ImageSizing.upload)
        XCTAssertEqual(sized.size.width * sized.scale, 1600)
        let jpeg: Data = try XCTUnwrap(photo.jpegData(compressionQuality: 0.8))
        let decoded: UIImage = try XCTUnwrap(ImageSizing.decoded(jpeg, longest: ImageSizing.upload))
        XCTAssertEqual(max(decoded.size.width, decoded.size.height) * decoded.scale, 1600, "decoded straight to the upload size")
    }

    /// The shimmer draws 30 frames a second while its band sweeps and none while it rests.
    func testTheShimmerDrawsOnlyWhileItsBandMoves() {
        let sweep: Double = 1.35
        let pause: Double = 1.45
        // A date of the reference clock in 2026, where rounding errors live.
        let start: Double = 2.8 * 286_000_000
        var time: Double = start
        var frames: [Double] = []
        while time < start + 2.8 * 3, frames.count < 1000 {
            frames.append(time)
            time = shimmerNextFrame(after: time, sweep: sweep, pause: pause, rate: 30)
        }
        // About 1.35 s × 30 frames plus the one past the end, a cycle: not 2.8 s × 120 of a free-running animation.
        XCTAssertEqual(Double(frames.count) / 3, 42, accuracy: 2)
        for (earlier, later) in zip(frames, frames.dropFirst()) {
            XCTAssertGreaterThanOrEqual(later - earlier, 0.0009, "never two frames at one moment")
            let position: Double = earlier.truncatingRemainder(dividingBy: sweep + pause)
            if position < sweep { XCTAssertLessThanOrEqual(later - earlier, 1.0 / 30 + 1e-9) }
            XCTAssertLessThan(shimmerPhase(at: later, sweep: sweep, pause: pause) - shimmerPhase(at: earlier, sweep: sweep, pause: pause), 0.1)
        }
        XCTAssertEqual(shimmerPhase(at: frames.last!, sweep: sweep, pause: pause), 1, accuracy: 0.05)
    }

    func testAHistogramsTotalAndMedian() {
        let buckets: [HistogramBucket] = [
            HistogramBucket(start: 0.25, end: 0.35, count: 3), HistogramBucket(start: 0.35, end: 0.45, count: 1),
            HistogramBucket(start: 1.0, end: 2.0, count: 1),
        ]
        XCTAssertEqual(histogramTotal(buckets), 3 * 0.3 + 0.4 + 1.5, accuracy: 1e-9)
        XCTAssertEqual(histogramMedian(buckets) ?? 0, 0.3, accuracy: 1e-9)
        XCTAssertNil(histogramMedian([]))
    }

    func testATimeOpenBecomesAReportOfWhatItUsed() {
        let traffic: (Int, Int) -> TrafficCounts = { bytes, opens in
            TrafficCounts(bytesIn: bytes, bytesOut: bytes / 10, requests: opens * 3, streamOpens: opens, streamBreaks: opens / 2)
        }
        let from: PerfReading = PerfReading(
            at: 100, wall: 1_800_000_000, usage: ProcessUsage(cpuNanoseconds: 1_000_000_000, wakeups: 10), traffic: traffic(1000, 2), publishes: 5, revealSteps: 0)
        let to: PerfReading = PerfReading(
            at: 700, wall: 1_800_000_600, usage: ProcessUsage(cpuNanoseconds: 4_000_000_000, wakeups: 90), traffic: traffic(51000, 6), publishes: 605, revealSteps: 40)
        let report: PhoneReport = appReport(from: from, to: to, openedAt: 1_800_000_000, appVersion: "1.0 (1)", osVersion: "iOS 26.1")
        XCTAssertEqual(report.source, .app)
        XCTAssertEqual(report.openS, 600)
        XCTAssertEqual(report.endsAt, 1_800_000_600)
        XCTAssertEqual(report.cpuS, 3)
        XCTAssertEqual(report.bytesIn, 50000)
        XCTAssertEqual(report.bytesOut, 5000)
        XCTAssertEqual(report.streamOpens, 4)
        XCTAssertEqual(report.streamBreaks, 2)
        XCTAssertEqual(report.redraws, 600)
        let stretch: PerfStretch = to.stretch(since: from, place: "chat")
        XCTAssertEqual(stretch.cpuMsPerSecond, 5, accuracy: 1e-9)
        XCTAssertEqual(stretch.publishesPerSecond, 1, accuracy: 1e-9)
        XCTAssertEqual(stretch.bytesInPerMinute, 5000, accuracy: 1e-9)
    }

    func testThePerformanceSectionSaysNoneForNoHangAndNoCrash() throws {
        let json: String = #"{"days":7,"reports":3,"last_at":1,"app_version":"1.0","foreground_min":0,"cpu_s":0,"hang_s":0,"launch_ms":null,"peak_memory_mb":null,"disk_writes_mb":0,"cellular_mb":0,"wifi_mb":0,"crashes":0,"hang_reports":0,"cpu_exceptions":0,"disk_write_exceptions":0,"open_min":12,"dot_in_mb":1.5,"dot_out_mb":0.25,"requests":10,"reconnects":1,"redraws_per_min":null}"#
        let summary: PhoneMetricsSummary = try JSONDecoder().decode(PhoneMetricsSummary.self, from: Data(json.utf8))
        let rows: [PerformanceRow] = performanceRows(summary)
        XCTAssertEqual(rows.map(\.id), ["hangs", "crashes", "data", "reconnects"], "a measure the phone has not sent is no row")
        XCTAssertEqual(rows.first { $0.id == "hangs" }?.detail, String(localized: "None", comment: "Maintenance › Performance: no hang, no crash"))
    }

    /// A whole answer is parsed once however often its row is drawn (owner 2026-11-27: scrolling a long chat stuttered
    /// while each row coming into sight parsed its answer anew), and its blocks are those of the parse.
    @MainActor func testAWholeAnswerIsParsedOnceHoweverOftenItsRowIsDrawn() {
        let answer: String = "## Page \(UUID().uuidString)\n\nThe **build** waited.\n\n| Part | Time |\n|---|---|\n| one | 37 ms |\n\n```ts\nload()\n```"
        let before: Int = PerfCounters.shared.answersParsed
        let first: [DrawnBlock] = DrawnAnswers.blocks(of: answer)
        let again: [DrawnBlock] = DrawnAnswers.blocks(of: answer)
        XCTAssertEqual(PerfCounters.shared.answersParsed - before, 1, "the second drawing parsed nothing")
        XCTAssertEqual(first.count, 4)
        XCTAssertEqual(again.count, first.count)
        guard case .table(let header, let rows, _) = first[2] else { return XCTFail("the third block is the table") }
        XCTAssertEqual(header.map { String($0.characters) }, ["Part", "Time"])
        XCTAssertEqual(rows.map { $0.map { String($0.characters) } }, [["one", "37 ms"]])
    }
}
