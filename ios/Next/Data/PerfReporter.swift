import DotEngine
import Foundation
import MetricKit
import UIKit
import os

/// The app's own performance record for the owner (item 1926): what iOS's MetricKit measures of the app (a day's CPU time,
/// hangs, launch, memory, disk writes, network, and the counts of its crash and hang reports) and the app's own counters
/// of each time it was open (`appReport`). The reports wait in a file of the app's caches and go to the owner's Mac
/// (`POST /api/phone/metrics`) when the app is open and paired: nothing is sent while it is in the background, and
/// nothing goes anywhere else. A report not sent stays for the next time (the newest `limit` are kept).
@MainActor
final class PerfReporter: NSObject, MXMetricManagerSubscriber {
    static let limit: Int = 200
    private let file: URL
    private var held: [PhoneReport]
    private var opened: PerfReading?
    private var openedWall: Double = 0
    private var sending: Bool = false
    private var started: Bool = false
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "perf")

    init(file: URL) {
        self.file = file
        held = Self.load(file)
        super.init()
    }

    /// The file the reports wait in, in the app's caches.
    static var defaultFile: URL { URL.cachesDirectory.appending(path: "dotnext-perf-reports.json") }

    /// Starts hearing MetricKit's deliveries (iOS hands them about once a day, and the diagnostics as they come).
    func start() {
        guard !started else { return }
        started = true
        MXMetricManager.shared.add(self)
    }

    /// The app came to the screen: a time open starts.
    func appeared() {
        opened = PerfCounters.shared.reading()
        openedWall = Date().timeIntervalSince1970
    }

    /// The app left the screen: the time it was open becomes a report, sent the next time it opens.
    func disappeared() {
        guard let from: PerfReading = opened, let to: PerfReading = PerfCounters.shared.reading() else { return }
        opened = nil
        let report: PhoneReport = appReport(
            from: from, to: to, openedAt: openedWall, appVersion: Self.appVersion, osVersion: Self.osVersion)
        add([report])
    }

    /// Sends the reports held, oldest first, `PhoneReports.most` at a time; those that could not go wait for the next.
    func send(with api: DaemonAPI) async {
        guard !sending else { return }
        sending = true
        defer { sending = false }
        while !held.isEmpty {
            let batch: [PhoneReport] = reportsToSend(held).send
            do {
                try await api.sendMetrics(batch)
            } catch {
                // Kept for the next time the app opens: the record is the owner's, not worth a retry loop now.
                logger.warning(
                    "performance reports not sent count=\(batch.count, privacy: .public) error=\(error.description, privacy: .public)")
                return
            }
            held = held.filter { !batch.contains($0) }
            save()
        }
    }

    private func add(_ reports: [PhoneReport]) {
        held = heldReports(held, adding: reports, limit: Self.limit)
        save()
    }

    private func save() {
        do {
            try JSONEncoder().encode(held).write(to: file, options: .atomic)
        } catch {
            logger.error("performance reports not kept path=\(self.file.path, privacy: .public) error=\(String(describing: error), privacy: .public)")
        }
    }

    private static func load(_ file: URL) -> [PhoneReport] {
        guard let data: Data = try? Data(contentsOf: file) else { return [] }
        do {
            return try JSONDecoder().decode([PhoneReport].self, from: data)
        } catch {
            Logger(subsystem: "dev.dotagent.phone.next", category: "perf").warning(
                "held performance reports unreadable, started again error=\(String(describing: error), privacy: .public)")
            return []
        }
    }

    static var appVersion: String {
        let short: String = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let build: String = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "\(short) (\(build))"
    }

    static var osVersion: String { "iOS \(UIDevice.current.systemVersion)" }

    // MARK: MetricKit

    nonisolated func didReceive(_ payloads: [MXMetricPayload]) {
        let reports: [PhoneReport] = payloads.map(metricReport)
        Task { @MainActor in self.add(reports) }
    }

    nonisolated func didReceive(_ payloads: [MXDiagnosticPayload]) {
        let reports: [PhoneReport] = payloads.map(diagnosticReport)
        Task { @MainActor in self.add(reports) }
    }
}

/// The report of one time the app was open, from the readings at its start and end. Pure.
func appReport(from: PerfReading, to: PerfReading, openedAt: Double, appVersion: String, osVersion: String) -> PhoneReport {
    let seconds: Double = max(0, to.at - from.at)
    let traffic: TrafficCounts = to.traffic.since(from.traffic)
    var report: PhoneReport = PhoneReport(source: .app, beginsAt: openedAt, endsAt: openedAt + seconds, appVersion: appVersion, osVersion: osVersion)
    report.openS = seconds
    report.cpuS = Double(to.usage.cpuNanoseconds &- from.usage.cpuNanoseconds) / 1_000_000_000
    report.bytesIn = traffic.bytesIn
    report.bytesOut = traffic.bytesOut
    report.requests = traffic.requests
    report.streamOpens = traffic.streamOpens
    report.streamBreaks = traffic.streamBreaks
    report.redraws = to.publishes - from.publishes
    return report
}

/// One bucket of a MetricKit histogram, in seconds.
struct HistogramBucket: Equatable, Sendable {
    let start: Double
    let end: Double
    let count: Int
}

/// The time a histogram holds in all, each bucket's count at its middle. Pure.
func histogramTotal(_ buckets: [HistogramBucket]) -> Double {
    buckets.reduce(0) { $0 + Double($1.count) * ($1.start + $1.end) / 2 }
}

/// The middle of the bucket the histogram's median falls in; `nil` for an empty one. Pure.
func histogramMedian(_ buckets: [HistogramBucket]) -> Double? {
    let total: Int = buckets.reduce(0) { $0 + $1.count }
    guard total > 0 else { return nil }
    var seen: Int = 0
    for bucket in buckets.sorted(by: { $0.start < $1.start }) {
        seen += bucket.count
        if seen * 2 >= total { return (bucket.start + bucket.end) / 2 }
    }
    return nil
}

private func buckets(_ histogram: MXHistogram<UnitDuration>) -> [HistogramBucket] {
    var read: [HistogramBucket] = []
    for case let bucket as MXHistogramBucket<UnitDuration> in histogram.bucketEnumerator {
        read.append(
            HistogramBucket(
                start: bucket.bucketStart.converted(to: .seconds).value, end: bucket.bucketEnd.converted(to: .seconds).value, count: bucket.bucketCount))
    }
    return read
}

private func mebibytes(_ value: Measurement<UnitInformationStorage>) -> Double { value.converted(to: .mebibytes).value }

/// A day of MetricKit's measures as a report: numbers only.
private func metricReport(_ payload: MXMetricPayload) -> PhoneReport {
    var report: PhoneReport = PhoneReport(
        source: .metrickit, beginsAt: payload.timeStampBegin.timeIntervalSince1970, endsAt: payload.timeStampEnd.timeIntervalSince1970,
        appVersion: payload.latestApplicationVersion, osVersion: payload.metaData?.osVersion ?? "")
    report.foregroundS = payload.applicationTimeMetrics?.cumulativeForegroundTime.converted(to: .seconds).value
    report.backgroundS = payload.applicationTimeMetrics?.cumulativeBackgroundTime.converted(to: .seconds).value
    report.cpuS = payload.cpuMetrics?.cumulativeCPUTime.converted(to: .seconds).value
    report.gpuS = payload.gpuMetrics?.cumulativeGPUTime.converted(to: .seconds).value
    report.hangS = payload.applicationResponsivenessMetrics.map { histogramTotal(buckets($0.histogrammedApplicationHangTime)) }
    report.launchMs = payload.applicationLaunchMetrics.flatMap { histogramMedian(buckets($0.histogrammedTimeToFirstDraw)) }.map { $0 * 1000 }
    report.peakMemoryMb = payload.memoryMetrics.map { mebibytes($0.peakMemoryUsage) }
    report.diskWritesMb = payload.diskIOMetrics.map { mebibytes($0.cumulativeLogicalWrites) }
    if let network: MXNetworkTransferMetric = payload.networkTransferMetrics {
        report.cellularMb = mebibytes(network.cumulativeCellularDownload) + mebibytes(network.cumulativeCellularUpload)
        report.wifiMb = mebibytes(network.cumulativeWifiDownload) + mebibytes(network.cumulativeWifiUpload)
    }
    report.scrollHitchMsPerS = payload.animationMetrics?.scrollHitchTimeRatio.value
    return report
}

/// A delivery of MetricKit's diagnostics as a report: how many of each, never their contents.
private func diagnosticReport(_ payload: MXDiagnosticPayload) -> PhoneReport {
    var report: PhoneReport = PhoneReport(
        source: .metrickit, beginsAt: payload.timeStampBegin.timeIntervalSince1970, endsAt: payload.timeStampEnd.timeIntervalSince1970,
        appVersion: payload.crashDiagnostics?.first?.metaData.applicationBuildVersion ?? payload.hangDiagnostics?.first?.metaData.applicationBuildVersion ?? "",
        osVersion: payload.crashDiagnostics?.first?.metaData.osVersion ?? payload.hangDiagnostics?.first?.metaData.osVersion ?? "")
    report.crashes = payload.crashDiagnostics?.count ?? 0
    report.hangReports = payload.hangDiagnostics?.count ?? 0
    report.cpuExceptions = payload.cpuExceptionDiagnostics?.count ?? 0
    report.diskWriteExceptions = payload.diskWriteExceptionDiagnostics?.count ?? 0
    return report
}
