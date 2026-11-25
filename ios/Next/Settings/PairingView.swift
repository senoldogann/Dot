import DotEngine
import SwiftUI
import UIKit

/// The first run (PLAN §4.3, the lead's order of 2026-11-25): Dot's face, a title and one line; the primary way is the
/// camera on the code Settings › Phone shows on the Mac (`dotnext://pair?host=…&code=…`, which fills both and pairs on
/// one tap; the address code fills the address alone), the second way the typed code. A link opened from elsewhere waits
/// for the same one tap. Each failure says what to do, and the last address is remembered.
struct PairingView: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var address: String = UserDefaults.standard.string(forKey: "lastPairAddress") ?? ""
    @State private var code: String = ""
    @State private var typing: Bool = false
    @State private var working: Bool = false
    @State private var problem: PairingProblem?
    @State private var scanning: Bool = false
    @FocusState private var focus: Field?

    enum Field: Hashable {
        case address
        case code
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Space.xl) {
                    VStack(spacing: Space.m) {
                        DotFace(size: 72)
                        Text(String(localized: "Pair with your Mac", comment: "Title of the pairing screen")).textRole(.title).foregroundStyle(Tone.text)
                            .multilineTextAlignment(.center)
                        Text(String(localized: "Dot Next talks to Dot on your Mac over Tailscale. On the Mac, open Dot › Settings › Phone and show a code.", comment: "Pairing screen: the one line under the title"))
                            .textRole(.body).foregroundStyle(Tone.secondary).multilineTextAlignment(.center)
                    }
                    .padding(.top, Space.xxl)
                    if let waiting = model.pairingStartWaiting, case .link(let host, let linkCode) = waiting {
                        linkCard(host: host, code: linkCode)
                    } else {
                        ways
                    }
                    if let problem { problemBlock(problem) }
                }
                .padding(Space.gutter)
                .readableColumn()
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Tone.ground)
            .sheet(isPresented: $scanning) {
                QRScanSheet { start in
                    switch start {
                    case .link: model.pairingStartWaiting = start
                    case .address(let host):
                        address = host.name
                        typing = true
                        focus = .code
                    }
                }
            }
            .onChange(of: model.pairingStartWaiting) { _, start in
                if case .address(let host) = start {
                    address = host.name
                    typing = true
                    model.pairingStartWaiting = nil
                }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
    }

    /// The two ways: scan (primary) and type (secondary, which opens the fields).
    @ViewBuilder private var ways: some View {
        VStack(spacing: Space.s) {
            Button(String(localized: "Scan the code on your Mac", comment: "Pairing screen: the primary way, the camera on the Mac's code"), systemImage: "qrcode.viewfinder") { scanning = true }
                .buttonStyle(PrimaryButtonStyle())
                .frame(maxWidth: .infinity)
            if !typing {
                Button(String(localized: "Enter the code", comment: "Pairing screen: the second way, typing the address and the code")) {
                    typing = true
                    focus = address.isEmpty ? .address : .code
                }
                .buttonStyle(SecondaryButtonStyle())
                .frame(maxWidth: .infinity)
            }
        }
        if typing {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                GroupedSection(
                    header: String(localized: "Your Mac's address", comment: "Pairing screen section: the Mac's address"),
                    footer: String(localized: "Settings › Phone on the Mac shows it; it ends in .ts.net.", comment: "Pairing screen: where the address is")
                ) {
                    TextField(String(localized: "mac.your-tailnet.ts.net", comment: "Pairing screen: placeholder of the Mac's address field"), text: $address)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL).textContentType(.URL)
                        .focused($focus, equals: .address).submitLabel(.next).onSubmit { focus = .code }
                        .textRole(.body).padding(Space.groupedGutter)
                        .accessibilityLabel(String(localized: "The Mac's address", comment: "VoiceOver: the pairing screen's address field"))
                }
                GroupedSection(
                    header: String(localized: "Sign-in code", comment: "Pairing screen section: the 8-character code"),
                    footer: String(localized: "8 letters and digits, good for 10 minutes and once.", comment: "Pairing screen: what the code is")
                ) {
                    TextField(String(localized: "7KQ2-M9XD", comment: "Pairing screen: placeholder of the code field"), text: $code)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled().textContentType(.oneTimeCode)
                        .font(.system(.title3, design: .monospaced))
                        .focused($focus, equals: .code).submitLabel(.go).onSubmit { Task { await pair(address: address, code: code) } }
                        .padding(Space.groupedGutter)
                        .accessibilityLabel(String(localized: "Sign-in code", comment: "Pairing screen section: the 8-character code"))
                }
                Button {
                    Task { await pair(address: address, code: code) }
                } label: {
                    HStack(spacing: Space.s) {
                        if working { ProgressView().tint(Tone.onAction) }
                        Text(working ? String(localized: "Pairing…", comment: "Pairing screen: the pairing is under way") : String(localized: "Pair", comment: "Pairing screen: the button that pairs the phone"))
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(working || address.trimmingCharacters(in: .whitespaces).isEmpty || normalizedPairingCode(code) == nil)
                .frame(maxWidth: .infinity)
            }
        }
    }

    /// A pair link (scanned or opened) waits for one tap.
    private func linkCard(host: DaemonHost, code linkCode: String) -> some View {
        VStack(spacing: Space.m) {
            VStack(spacing: Space.xxs) {
                Text(host.name).textRole(.headline).foregroundStyle(Tone.text).multilineTextAlignment(.center)
                Text(String(localized: "Code \(String(linkCode.prefix(4)))-\(String(linkCode.suffix(4)))", comment: "Pairing screen: the code a link carries"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary).monospaced()
            }
            .frame(maxWidth: .infinity)
            .padding(Space.l)
            .background(Tone.card, in: rounded(Radius.section))
            Button {
                Task { await pair(address: host.origin.absoluteString, code: linkCode) }
            } label: {
                HStack(spacing: Space.s) {
                    if working { ProgressView().tint(Tone.onAction) }
                    Text(working ? String(localized: "Pairing…", comment: "Pairing screen: the pairing is under way") : String(localized: "Pair with this Mac", comment: "Pairing screen: one tap pairs with a link's Mac"))
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(working)
            .frame(maxWidth: .infinity)
            Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits")) {
                model.pairingStartWaiting = nil
                problem = nil
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(working)
        }
    }

    private func problemBlock(_ problem: PairingProblem) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Notice(tone: .failure, message: pairingWords(problem), dismiss: { self.problem = nil })
            if case .reach(.nameNotFound) = problem {
                Button(String(localized: "Open Tailscale", comment: "Pairing screen: opens the Tailscale app")) { openTailscale() }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
    }

    /// Tailscale's app, or its App Store page when it is not on this iPhone.
    private func openTailscale() {
        guard let app = URL(string: "tailscale://"), let store = URL(string: "https://apps.apple.com/app/tailscale/id1470499037") else { return }
        UIApplication.shared.open(app) { opened in
            if !opened { UIApplication.shared.open(store) }
        }
    }

    private func pair(address: String, code: String) async {
        guard !working else { return }
        working = true
        problem = await model.pair(address: address, code: code)
        working = false
        UINotificationFeedbackGenerator().notificationOccurred(problem == nil ? .success : .error)
    }
}

/// What to do about a failed pairing, in one line.
func pairingWords(_ problem: PairingProblem) -> String {
    switch problem {
    case .address(.notHTTPS):
        String(localized: "The address must start with https://, or have no prefix at all.", comment: "Pairing error: not https")
    case .address(.notTailnet(let host)):
        String(localized: "\(host) is not a Tailscale address. It ends in .ts.net; Settings › Phone on the Mac shows it.", comment: "Pairing error: the address is not under ts.net; the argument is the host")
    case .address(.hasPort), .address(.hasPath), .address(.hasCredentials), .address(.malformed), .address(.empty), .address(.notBuildHost):
        String(localized: "Type your Mac's tailnet address, like mac.your-tailnet.ts.net.", comment: "Pairing error: the address is not a tailnet address")
    case .pairing(.malformedCode): String(localized: "The code is 8 letters and digits.", comment: "Pairing error: the code has the wrong length")
    case .pairing(.codeRefused):
        String(localized: "That code is wrong, used or out of date. Show a new one on your Mac: Settings › Phone.", comment: "Pairing error: the Mac refused the code")
    case .pairing(.tooManyPhones):
        String(localized: "Your Mac pairs no more phones. Remove one on the Mac in Settings › Phone, then try again.", comment: "Pairing error: the Mac holds keys for as many phones as it takes")
    case .pairing(.daemonTooOld):
        String(localized: "Update Dot on your Mac: it is too old for this app.", comment: "Pairing error: the Mac's Dot lacks the sync calls")
    case .pairing(.unreachable):
        String(localized: "Your Mac could not be reached. Check that it is on and on your tailnet.", comment: "Pairing error: no answer from the Mac")
    case .pairing(.keychain):
        String(localized: "The key could not be kept on this phone.", comment: "Pairing error: the Keychain refused to keep the key")
    case .reach(let hop): problemWords(hop)
    }
}
