import SwiftUI

/// Trades the code Settings › Phone on the Mac shows for this phone's own key.
struct PairView: View {
    let model: AppModel
    let host: DaemonHost
    @State private var code: String = ""
    @State private var failure: String?
    @State private var working: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            DotGlyph(size: 48)
            Text("Pair this phone").font(.system(size: 22, weight: .semibold)).foregroundStyle(Theme.text)
            Text("On your Mac, open Dot › Settings › Phone and press for a new code. Type it here.")
                .font(Theme.small).foregroundStyle(Theme.muted)
            TextField("7KQ2-M9XD", text: $code)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .font(.system(size: 20, weight: .medium, design: .monospaced))
                .padding(12)
                .card()
                .onSubmit(pair)
            if let failure { Text(failure).font(Theme.small).foregroundStyle(Theme.bad) }
            Button(action: pair) {
                Text(working ? "Pairing…" : "Pair").frame(maxWidth: .infinity)
            }
            .buttonStyle(QuietButtonStyle(tint: Theme.accent, filled: true))
            .disabled(code.trimmingCharacters(in: .whitespaces).isEmpty || working)
            HStack {
                Button("Pair in the web UI instead") { model.showsWeb = true }
                Spacer()
                Button("Change address") { model.showsSettings = true }
            }
            .font(Theme.meta).foregroundStyle(Theme.muted)
            Spacer()
        }
        .padding(24)
        .background(Theme.background)
    }

    private func pair() {
        guard !working else { return }
        working = true
        failure = nil
        let client: APIClient = APIClient(host: host, key: "", session: APIClient.makeSession())
        let typed: String = code
        Task {
            defer { working = false }
            do {
                let key: String = try await client.pair(code: typed)
                model.didPair(key: key)
            } catch DaemonRequestError.status(_, let status, _) where status == 400 || status == 401 || status == 403 || status == 422 {
                failure = "That code is wrong, used or out of date. Ask for a new one on your Mac."
            } catch {
                failure = "Dot can't reach \(host.name). Is the Mac awake and Tailscale on?"
            }
        }
    }
}
