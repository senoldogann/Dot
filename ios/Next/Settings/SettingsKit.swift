import DotEngine
import SwiftUI
import UIKit

/// A page of Settings, pushed on its stack (iPhone) or shown in the detail (iPad).
enum SettingsPage: Hashable, Sendable {
    case mac
    case notifications
    case general
    case appearance
    case chat
    case models
    case caps
    case schedules
    case rules
    case memory
    case agents
    case system
    case usage
    case maintenance
    case campaigns
    case campaign(String)
    /// The Connect apps sheet (item 1862): a search result that opens it, never pushed.
    case connectApps
}

/// What a page read from the Mac: still on its way, there, or why not.
enum Loadable<Value: Sendable>: Sendable {
    case loading
    case loaded(Value)
    case failed(SettingsFailure)

    var value: Value? {
        if case .loaded(let value) = self { return value }
        return nil
    }
}

extension AppModel {
    /// The daemon Settings reads: the paired Mac's, or (debug builds) a fixture's.
    var settingsAPI: DaemonAPI? {
        if let engine { return engine.api }
        return fixtureAPI
    }

    /// One read of a settings page.
    func read<Value: Sendable>(_ call: (DaemonAPI) async throws(DaemonRequestError) -> Value) async -> Loadable<Value> {
        guard let api: DaemonAPI = settingsAPI else { return .failed(.unreachable) }
        do throws(DaemonRequestError) {
            return .loaded(try await call(api))
        } catch {
            logger.warning("a settings read failed error=\(String(describing: error), privacy: .public)")
            return .failed(settingsFailure(error))
        }
    }

    /// One write of a settings page: the new value, or why not (the page puts the old value back).
    func write<Value: Sendable>(_ call: (DaemonAPI) async throws(DaemonRequestError) -> Value) async -> Result<Value, SettingsFailureBox> {
        guard let api: DaemonAPI = settingsAPI else { return .failure(SettingsFailureBox(failure: .unreachable)) }
        do throws(DaemonRequestError) {
            return .success(try await call(api))
        } catch {
            logger.warning("a settings write failed error=\(String(describing: error), privacy: .public)")
            return .failure(SettingsFailureBox(failure: settingsFailure(error)))
        }
    }
}

/// A `SettingsFailure` as an `Error`, for a `Result`.
struct SettingsFailureBox: Error, Equatable {
    let failure: SettingsFailure
}

/// The words of a failure (STATES-TEMPLATE.md rows 8-13). `macPlace` names where the same thing is done on the Mac
/// ("Settings › Models"), for the continuation of a refusal.
func failureWords(_ failure: SettingsFailure, macPlace: String) -> String {
    switch failure {
    case .unreachable:
        String(localized: "Your Mac could not be reached.", comment: "Settings: a read or write got no answer from the Mac")
    case .keyRefused:
        String(localized: "Your Mac refused this phone's key. Pair again.", comment: "Settings: 401, the phone's key is no longer taken")
    case .notForAPhone(let words):
        String(
            localized: "Your Mac does not take this from a phone (\(words)). Open on the Mac: \(macPlace).",
            comment: "Settings: the daemon refused a phone's key; first argument its words, second where on the Mac")
    case .daemonTooOld:
        String(localized: "Update Dot on your Mac: this page needs a newer Dot.", comment: "Settings: the Mac's Dot lacks this page's route")
    case .unreadable(let path):
        String(
            localized: "Your Mac answered with what this app cannot read (\(path)). Update Dot on the Mac or this app.",
            comment: "Settings: the daemon's answer did not decode; the argument is the route")
    case .other(let words):
        String(localized: "That did not work: \(words)", comment: "Settings: a read or write failed; the argument is the daemon's words")
    }
}

/// A failure in a page: the notice and what can be done about it.
struct FailureBanner: View {
    let failure: SettingsFailure
    let macPlace: String
    let retry: (() -> Void)?
    @Environment(AppModel.self) private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Notice(tone: .failure, message: failureWords(failure, macPlace: macPlace), dismiss: nil)
            HStack(spacing: Space.s) {
                switch failure {
                case .unreachable:
                    NavigationLink(value: SettingsPage.mac) {
                        Text(String(localized: "Diagnose", comment: "Settings: opens the connection's diagnosis"))
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    if let retry { retryButton(retry) }
                case .keyRefused:
                    Button(String(localized: "Pair again", comment: "Settings: forgets the refused key and opens pairing")) { Task { await model.forget() } }
                        .buttonStyle(SecondaryButtonStyle())
                case .notForAPhone:
                    Button(String(localized: "Copy instructions", comment: "Settings: copies where to do a refused thing on the Mac")) {
                        UIPasteboard.general.string = failureWords(failure, macPlace: macPlace)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                case .daemonTooOld:
                    EmptyView()
                case .unreadable:
                    if let retry { retryButton(retry) }
                case .other:
                    if let retry { retryButton(retry) }
                }
            }
        }
    }

    private func retryButton(_ retry: @escaping () -> Void) -> some View {
        Button(String(localized: "Try again", comment: "Settings: repeats a read or write that failed"), action: retry).buttonStyle(SecondaryButtonStyle())
    }
}

/// A page of grouped sections on the ground, as every Settings page is drawn.
struct SettingsScroll<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) { content() }
                .padding(.horizontal, Space.gutter)
                .padding(.vertical, Space.groupedGutter)
                .readableColumn()
        }
        .background(Tone.ground)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// A page that reads one thing first: the spinner while it comes, the failure with its way out, the content once there.
struct LoadedPage<Value: Sendable, Content: View>: View {
    let state: Loadable<Value>
    let macPlace: String
    let retry: () -> Void
    @ViewBuilder let content: (Value) -> Content

    var body: some View {
        switch state {
        case .loading:
            LoadingState(message: String(localized: "Reading your Mac…", comment: "Settings: a page waits for its first read")).frame(minHeight: 240)
        case .failed(let failure):
            FailureBanner(failure: failure, macPlace: macPlace, retry: retry)
        case .loaded(let value):
            content(value)
        }
    }
}

/// The "Web" mark of a row that opens a page of the Mac's web app (Watch the Mac): a quiet word in a nested capsule.
struct WebMark: View {
    var body: some View {
        Text(String(localized: "Web", comment: "Settings: the mark of a row that opens the web page in the app"))
            .textRole(.label).fontWeight(.medium).foregroundStyle(Tone.secondary)
            .padding(.horizontal, Space.xs).padding(.vertical, 2)
            .background(Tone.nested, in: Capsule())
            .accessibilityLabel(String(localized: "Opens the web page", comment: "VoiceOver: the Web mark of a settings row"))
    }
}

/// A row that opens a page of Settings.
struct PageRow: View {
    let page: SettingsPage
    let systemImage: String
    let title: String
    let description: String?
    let detail: String?

    var body: some View {
        NavigationLink(value: page) {
            ListRow(systemImage: systemImage, title: title, description: description, accessory: detail.map { RowAccessory.detailAndChevron($0) } ?? .chevron)
        }
        .buttonStyle(RowPressStyle())
    }
}

/// Tokens as the owner reads them: "512M", "4,000M", "2.3M", "45k" (the web's `tokens_words`).
func tokenWords(_ tokens: Int, locale: Locale) -> String {
    let million: Double = 1_000_000
    if tokens >= 100_000_000 { return "\((Double(tokens) / million).formatted(.number.precision(.fractionLength(0)).locale(locale)))M" }
    if tokens >= 1_000_000 { return "\((Double(tokens) / million).formatted(.number.precision(.fractionLength(0...1)).locale(locale)))M" }
    if tokens >= 1000 { return "\(tokens / 1000)k" }
    return "\(tokens)"
}
