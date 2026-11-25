import Foundation

/// A hop of the way from this phone to the daemon's answer with its key (research G2 #5, PLAN §17 #14), in the order a
/// request takes them: the phone's own network, the tailnet name, the Mac, the daemon, the key.
public enum DiagnosisHop: String, CaseIterable, Sendable, Codable {
    case phone
    case tailnet
    case mac
    case daemon
    case key
}

/// What one probe of the daemon came back with.
public enum ProbeAnswer: Sendable, Equatable {
    /// The daemon answered with this HTTP status after `millis`.
    case answered(code: Int, millis: Int)
    /// No HTTP answer: the URL loading system's code.
    case transport(code: Int, millis: Int)
    /// The probe was not made (an earlier hop failed).
    case notRun
}

/// What the probes saw: the link, the public health route without the key, the status route with it, and whether the
/// daemon proved it holds this phone's key (`GET /api/proof`).
public struct DiagnosisProbe: Sendable, Equatable {
    public let link: LinkState
    public let health: ProbeAnswer
    public let status: ProbeAnswer
    public let proof: ProofOutcome

    public init(link: LinkState, health: ProbeAnswer, status: ProbeAnswer, proof: ProofOutcome) {
        self.link = link
        self.health = health
        self.status = status
        self.proof = proof
    }
}

/// Whether the daemon proved it knows this phone's key.
public enum ProofOutcome: Sendable, Equatable {
    case proven
    /// The daemon answered with a proof that does not match the key: not this phone's Mac, or a key it no longer holds.
    case mismatch
    case notRun
    /// The daemon has no proof route (a Dot from before it).
    case unsupported
}

/// Why a hop failed, as the words of the page name it.
public enum HopProblem: Sendable, Equatable {
    case noNetwork
    /// The `.ts.net` name does not resolve: Tailscale is off on this phone, or signed out.
    case nameNotFound
    /// The name resolved but nothing answered: the Mac sleeps, is off, or its Tailscale is off.
    case macSilent
    /// The connection was refused: the Mac answers but nothing listens on Dot's port (Dot is not running).
    case daemonNotListening
    /// The secure connection failed (a certificate the phone does not trust).
    case insecure
    /// The daemon answered with an error status.
    case daemonError(Int)
    /// The daemon refused this phone's key (401/403).
    case keyRefused(Int)
    /// The daemon's proof does not match the key.
    case proofMismatch
    /// The daemon is too old for this app.
    case daemonTooOld
    /// Something the classification does not know; the code is in the report.
    case other(Int)
}

/// One hop's result.
public enum HopResult: Sendable, Equatable {
    case passed
    case failed(HopProblem)
    /// Not tried, because an earlier hop failed.
    case unknown
}

/// The whole diagnosis: each hop's result in order, and the first that failed.
public struct Diagnosis: Sendable, Equatable {
    public let hops: [(DiagnosisHop, HopResult)]
    public var firstFailure: (DiagnosisHop, HopProblem)? {
        for (hop, result) in hops { if case .failed(let problem) = result { return (hop, problem) } }
        return nil
    }

    public static func == (lhs: Diagnosis, rhs: Diagnosis) -> Bool {
        lhs.hops.map(\.0) == rhs.hops.map(\.0) && lhs.hops.map(\.1) == rhs.hops.map(\.1)
    }
}

/// The URL loading system's codes the classification reads (`URLError.Code` raw values, so tests need no URLError).
enum LoadingCode {
    static let notConnected: Int = -1009
    static let cannotFindHost: Int = -1003
    static let dnsLookupFailed: Int = -1006
    static let timedOut: Int = -1001
    static let cannotConnectToHost: Int = -1004
    static let networkConnectionLost: Int = -1005
    static let secureConnectionFailed: Int = -1200
    static let certificateCodes: ClosedRange<Int> = -1206 ... -1201
}

/// Reads the probes into hops (pure): the first hop that failed explains the others, which stay unknown.
public func diagnose(_ probe: DiagnosisProbe) -> Diagnosis {
    var hops: [(DiagnosisHop, HopResult)] = []
    func failAt(_ hop: DiagnosisHop, _ problem: HopProblem) -> Diagnosis {
        hops.append((hop, .failed(problem)))
        for later in DiagnosisHop.allCases.drop(while: { $0 != hop }).dropFirst() { hops.append((later, .unknown)) }
        return Diagnosis(hops: hops)
    }
    guard probe.link.online else { return failAt(.phone, .noNetwork) }
    hops.append((.phone, .passed))
    switch probe.health {
    case .notRun:
        return failAt(.tailnet, .other(0))
    case .transport(let code, _):
        if code == LoadingCode.notConnected { return failAt(.tailnet, .noNetwork) }
        if code == LoadingCode.cannotFindHost || code == LoadingCode.dnsLookupFailed { return failAt(.tailnet, .nameNotFound) }
        hops.append((.tailnet, .passed))
        if code == LoadingCode.timedOut || code == LoadingCode.networkConnectionLost { return failAt(.mac, .macSilent) }
        hops.append((.mac, .passed))
        if code == LoadingCode.cannotConnectToHost { return failAt(.daemon, .daemonNotListening) }
        if code == LoadingCode.secureConnectionFailed || LoadingCode.certificateCodes.contains(code) { return failAt(.daemon, .insecure) }
        return failAt(.daemon, .other(code))
    case .answered(let code, _):
        hops.append((.tailnet, .passed))
        hops.append((.mac, .passed))
        guard (200..<300).contains(code) else { return failAt(.daemon, .daemonError(code)) }
        hops.append((.daemon, .passed))
    }
    switch probe.status {
    case .answered(let code, _) where code == 401 || code == 403:
        return failAt(.key, .keyRefused(code))
    case .answered(let code, _) where !(200..<300).contains(code):
        return failAt(.key, .daemonError(code))
    case .answered:
        break
    case .transport(let code, _):
        return failAt(.key, .other(code))
    case .notRun:
        return failAt(.key, .other(0))
    }
    if probe.proof == .mismatch { return failAt(.key, .proofMismatch) }
    hops.append((.key, .passed))
    return Diagnosis(hops: hops)
}

/// A short id for one diagnosis, so the report the owner pastes and the Mac's request log can be put side by side.
public func traceId(random: UInt64) -> String {
    String(random, radix: 36).padding(toLength: 10, withPad: "0", startingAt: 0).prefix(10).uppercased()
}

/// The Mac's address from the QR code Settings › Phone shows (`GET /api/phone/qr/dot`: `https://<reach>/`, the address
/// only; the sign-in code is still typed, as a QR carrying it needs a daemon change under "Always needs a human").
/// `nil` for anything that is not a tailnet daemon address, so a stray code fills nothing.
public func addressFromQR(_ payload: String) -> DaemonHost? {
    try? parseDaemonHost(payload, buildHost: nil).get()
}

/// What a scanned code or an opened link gives the pairing screen.
public enum PairingStart: Sendable, Equatable {
    /// `dotnext://pair?host=https://<mac>.ts.net&code=<8 characters>` (Settings › Phone's QR for Dot Next): both, so
    /// one tap pairs.
    case link(host: DaemonHost, code: String)
    /// `https://<mac>.ts.net/` (the address QR): the code is still typed.
    case address(DaemonHost)
}

/// Reads a scanned code or an opened `dotnext://` link (pure). Only a tailnet address and a code of 8 letters and digits
/// are taken; anything else is no start, so a stray link fills nothing and sends nothing.
public func pairingStart(_ text: String) -> PairingStart? {
    let trimmed: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if let components: URLComponents = URLComponents(string: trimmed), components.scheme == "dotnext" {
        guard components.host == "pair", components.path.isEmpty || components.path == "/" else { return nil }
        let items: [URLQueryItem] = components.queryItems ?? []
        guard let hostText: String = items.first(where: { $0.name == "host" })?.value, hostText.lowercased().hasPrefix("https://"),
            let host: DaemonHost = try? parseDaemonHost(hostText, buildHost: nil).get(),
            let code: String = items.first(where: { $0.name == "code" })?.value.flatMap(normalizedPairingCode)
        else { return nil }
        return .link(host: host, code: code)
    }
    return addressFromQR(trimmed).map(PairingStart.address)
}
