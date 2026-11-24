// swift-tools-version: 6.2
import PackageDescription

/// DotNext's engine: the daemon's contract, the sync store, the connection supervisor, the outbox, the cache and the
/// pairing. Foundation, Network and Security only, so it builds for iOS and for the macOS app that follows (owner,
/// 2026-11-24); no UIKit or SwiftUI in here.
let package = Package(
    name: "DotEngine",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [.library(name: "DotEngine", targets: ["DotEngine"])],
    targets: [
        .target(name: "DotEngine", swiftSettings: [.swiftLanguageMode(.v6)]),
        .testTarget(name: "DotEngineTests", dependencies: ["DotEngine"], swiftSettings: [.swiftLanguageMode(.v6)]),
    ]
)
