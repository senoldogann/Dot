import Foundation

// The phone's own performance record (item 1926): `POST /api/phone/metrics` and its summary, `GET /api/phone/metrics`.

/// One report of the phone's: `metrickit` (a day of iOS's measures, or the counts of a diagnostics delivery) or `app`
/// (one time the app was open, its own counters). A measure a report does not carry is `nil`, and left out of the JSON.
public struct PhoneReport: Codable, Sendable, Equatable {
    public enum Source: String, Codable, Sendable {
        case metrickit
        case app
    }

    public let source: Source
    public let beginsAt: Double
    public let endsAt: Double
    public let appVersion: String
    public let osVersion: String
    public var foregroundS: Double?
    public var backgroundS: Double?
    public var cpuS: Double?
    public var gpuS: Double?
    public var hangS: Double?
    public var launchMs: Double?
    public var peakMemoryMb: Double?
    public var diskWritesMb: Double?
    public var cellularMb: Double?
    public var wifiMb: Double?
    public var scrollHitchMsPerS: Double?
    public var crashes: Int?
    public var hangReports: Int?
    public var cpuExceptions: Int?
    public var diskWriteExceptions: Int?
    public var openS: Double?
    public var bytesIn: Int?
    public var bytesOut: Int?
    public var requests: Int?
    public var streamOpens: Int?
    public var streamBreaks: Int?
    public var redraws: Int?

    /// A report with no measure yet: the caller sets those it has.
    public init(source: Source, beginsAt: Double, endsAt: Double, appVersion: String, osVersion: String) {
        self.source = source
        self.beginsAt = beginsAt
        self.endsAt = endsAt
        self.appVersion = String(appVersion.prefix(40))
        self.osVersion = String(osVersion.prefix(40))
    }

    enum CodingKeys: String, CodingKey {
        case source
        case beginsAt = "begins_at"
        case endsAt = "ends_at"
        case appVersion = "app_version"
        case osVersion = "os_version"
        case foregroundS = "foreground_s"
        case backgroundS = "background_s"
        case cpuS = "cpu_s"
        case gpuS = "gpu_s"
        case hangS = "hang_s"
        case launchMs = "launch_ms"
        case peakMemoryMb = "peak_memory_mb"
        case diskWritesMb = "disk_writes_mb"
        case cellularMb = "cellular_mb"
        case wifiMb = "wifi_mb"
        case scrollHitchMsPerS = "scroll_hitch_ms_per_s"
        case crashes
        case hangReports = "hang_reports"
        case cpuExceptions = "cpu_exceptions"
        case diskWriteExceptions = "disk_write_exceptions"
        case openS = "open_s"
        case bytesIn = "bytes_in"
        case bytesOut = "bytes_out"
        case requests
        case streamOpens = "stream_opens"
        case streamBreaks = "stream_breaks"
        case redraws
    }
}

/// The body of `POST /api/phone/metrics`: at most `PhoneReports.most` reports, oldest first.
public struct PhoneReports: Codable, Sendable, Equatable {
    public static let most: Int = 50
    public let reports: [PhoneReport]

    public init(reports: [PhoneReport]) { self.reports = reports }
}

/// `GET /api/phone/metrics`: the last `days` days of this phone's reports, summed (the launch the newest, the memory
/// the highest).
public struct PhoneMetricsSummary: Codable, Sendable, Equatable {
    public let days: Int
    public let reports: Int
    public let lastAt: Double?
    public let appVersion: String?
    public let foregroundMin: Double
    public let cpuS: Double
    public let hangS: Double
    public let launchMs: Double?
    public let peakMemoryMb: Double?
    public let diskWritesMb: Double
    public let cellularMb: Double
    public let wifiMb: Double
    public let crashes: Int
    public let hangReports: Int
    public let cpuExceptions: Int
    public let diskWriteExceptions: Int
    public let openMin: Double
    public let dotInMb: Double
    public let dotOutMb: Double
    public let requests: Int
    public let reconnects: Int
    public let redrawsPerMin: Double?

    enum CodingKeys: String, CodingKey {
        case days
        case reports
        case lastAt = "last_at"
        case appVersion = "app_version"
        case foregroundMin = "foreground_min"
        case cpuS = "cpu_s"
        case hangS = "hang_s"
        case launchMs = "launch_ms"
        case peakMemoryMb = "peak_memory_mb"
        case diskWritesMb = "disk_writes_mb"
        case cellularMb = "cellular_mb"
        case wifiMb = "wifi_mb"
        case crashes
        case hangReports = "hang_reports"
        case cpuExceptions = "cpu_exceptions"
        case diskWriteExceptions = "disk_write_exceptions"
        case openMin = "open_min"
        case dotInMb = "dot_in_mb"
        case dotOutMb = "dot_out_mb"
        case requests
        case reconnects
        case redrawsPerMin = "redraws_per_min"
    }
}

/// The reports to send next: those held, oldest first, at most `PhoneReports.most`; the rest wait for the next send.
/// Pure.
public func reportsToSend(_ held: [PhoneReport]) -> (send: [PhoneReport], keep: [PhoneReport]) {
    (Array(held.prefix(PhoneReports.most)), Array(held.dropFirst(PhoneReports.most)))
}

/// The reports held after `new` joined them: the newest `limit`, oldest first (a phone that cannot reach its Mac for
/// weeks keeps only the latest). Pure.
public func heldReports(_ held: [PhoneReport], adding new: [PhoneReport], limit: Int) -> [PhoneReport] {
    Array((held + new).suffix(limit))
}
