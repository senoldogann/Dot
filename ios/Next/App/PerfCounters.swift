import DotEngine
import Darwin
import Foundation

/// The app's own counters of what drawing the chats costs: how many times the app's model published a new picture of
/// the store (`publishes`, each redraws what reads the part that changed) and how many steps the answer's reveal drew
/// (`revealSteps`). The performance record (Settings › Maintenance) and the streaming check read them.
@MainActor
final class PerfCounters {
    static let shared: PerfCounters = PerfCounters()

    private(set) var publishes: Int = 0
    private(set) var revealSteps: Int = 0

    func published() { publishes += 1 }

    func revealed() { revealSteps += 1 }
}

/// What the process used since it started, read from the kernel: CPU time in nanoseconds, user and system, of every
/// thread (`getrusage`), and the wakeups that brought a core out of idle for it (`task_info` of `TASK_POWER_INFO`:
/// interrupt and idle wakeups), the two numbers Xcode's energy gauge is made of.
struct ProcessUsage: Equatable, Sendable {
    let cpuNanoseconds: UInt64
    let wakeups: UInt64

    /// The process's usage now; `nil` if the kernel refuses (it does not for the process itself).
    static func now() -> ProcessUsage? {
        var usage: rusage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        let nanoseconds: (timeval) -> UInt64 = { UInt64($0.tv_sec) * 1_000_000_000 + UInt64($0.tv_usec) * 1000 }
        var info: task_power_info = task_power_info()
        var count: mach_msg_type_number_t = mach_msg_type_number_t(MemoryLayout<task_power_info>.size / MemoryLayout<natural_t>.size)
        let result: kern_return_t = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_POWER_INFO), $0, &count) }
        }
        guard result == KERN_SUCCESS else { return nil }
        return ProcessUsage(
            cpuNanoseconds: nanoseconds(usage.ru_utime) + nanoseconds(usage.ru_stime),
            wakeups: info.task_interrupt_wakeups + info.task_platform_idle_wakeups)
    }
}

/// One stretch of the app's life measured: where the owner was (`chat`, `list`, `background`), how long, and what the
/// process and the engine used in it, per second or per minute. Pure arithmetic over two readings.
struct PerfStretch: Equatable, Sendable {
    let place: String
    /// When the stretch ended, Unix seconds (the streaming check matches the daemon's side to it).
    let endedAt: Double
    let seconds: Double
    let cpuMsPerSecond: Double
    let wakeupsPerSecond: Double
    let publishesPerSecond: Double
    let revealStepsPerSecond: Double
    let bytesInPerMinute: Double
    let bytesOutPerMinute: Double
    let requests: Int
    let streamOpens: Int

    /// The line the streaming check reads.
    var line: String {
        String(
            format: "place=%@ ended_at=%.1f seconds=%.1f cpu_ms_per_s=%.1f wakeups_per_s=%.1f publishes_per_s=%.1f reveal_steps_per_s=%.1f bytes_in_per_min=%.0f bytes_out_per_min=%.0f requests=%d stream_opens=%d\n",
            place, endedAt, seconds, cpuMsPerSecond, wakeupsPerSecond, publishesPerSecond, revealStepsPerSecond, bytesInPerMinute, bytesOutPerMinute, requests,
            streamOpens)
    }
}

/// A reading of every counter at one moment.
struct PerfReading: Equatable, Sendable {
    let at: Double
    /// The wall clock at the reading, Unix seconds.
    let wall: Double
    let usage: ProcessUsage
    let traffic: TrafficCounts
    let publishes: Int
    let revealSteps: Int

    /// What was used from `earlier` to this reading, in `place`. Pure.
    func stretch(since earlier: PerfReading, place: String) -> PerfStretch {
        let seconds: Double = max(at - earlier.at, 0.001)
        let traffic: TrafficCounts = traffic.since(earlier.traffic)
        return PerfStretch(
            place: place, endedAt: wall, seconds: seconds,
            cpuMsPerSecond: Double(usage.cpuNanoseconds &- earlier.usage.cpuNanoseconds) / 1_000_000 / seconds,
            wakeupsPerSecond: Double(usage.wakeups &- earlier.usage.wakeups) / seconds,
            publishesPerSecond: Double(publishes - earlier.publishes) / seconds,
            revealStepsPerSecond: Double(revealSteps - earlier.revealSteps) / seconds,
            bytesInPerMinute: Double(traffic.bytesIn) * 60 / seconds, bytesOutPerMinute: Double(traffic.bytesOut) * 60 / seconds,
            requests: traffic.requests, streamOpens: traffic.streamOpens)
    }
}

extension PerfCounters {
    /// Every counter now; `nil` only if the kernel refused the process's usage.
    func reading() -> PerfReading? {
        guard let usage: ProcessUsage = ProcessUsage.now() else { return nil }
        return PerfReading(
            at: ProcessInfo.processInfo.systemUptime, wall: Date().timeIntervalSince1970, usage: usage, traffic: Traffic.shared.counts, publishes: publishes, revealSteps: revealSteps)
    }
}

/// Debug builds with `-DotNextFrameMeter 1`: cuts the app's life into stretches by where the owner is (a chat open, the
/// list, the app in the background) and writes one `PerfStretch` line per stretch to `Library/Caches/dotnext-phases.txt`
/// (`scripts/e2e_streaming.sh` prints them). A stretch in the background is written when the app comes back: the
/// counters are cumulative, so what the process used while away is in it.
@MainActor
enum PhaseMeter {
    private static var place: String = "list"
    private static var foreground: String = "list"
    private static var started: PerfReading?

    /// The app came back: the place it left from.
    static func resume() { enter(foreground) }

    static func enter(_ next: String) {
        if next != "background" { foreground = next }
        guard FrameMeter.enabled, let now: PerfReading = PerfCounters.shared.reading() else { return }
        if let started, next != place {
            write(now.stretch(since: started, place: place).line)
        }
        if next != place || started == nil { started = now }
        place = next
    }

    private static func write(_ line: String) {
        let file: URL = URL.cachesDirectory.appending(path: "dotnext-phases.txt")
        let data: Data = Data(line.utf8)
        if let handle: FileHandle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: file)
        }
    }
}
