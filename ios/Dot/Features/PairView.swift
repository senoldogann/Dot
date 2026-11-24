import SwiftUI

/// Trades the code Settings › Phone on the Mac shows for this phone's own key.
struct PairView: View {
    let model: AppModel
    let host: DaemonHost
    @State private var code: String = ""
    @State private var failure: String?
    @State private var working: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.l) {
                VStack(spacing: Space.m) {
                    DotGlyph(size: 72)
                        .shadow(color: Theme.accent.opacity(0.45), radius: 24, y: 6)
                    VStack(spacing: Space.xs) {
                        Text("Pair this phone").font(.title.weight(.semibold)).foregroundStyle(Theme.text)
                        Text(host.name).font(Theme.meta.weight(.medium)).foregroundStyle(Theme.faint)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.xxl).padding(.bottom, Space.s)
                VStack(alignment: .leading, spacing: 0) {
                    step(1, "On your Mac, open Dot › Settings › Phone.")
                    CardDivider(inset: 52)
                    step(2, "Press for a new code, and type it here.")
                }
                .card()
                TextField("", text: $code, prompt: Text("7KQ2-M9XD").foregroundStyle(Theme.muted))
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .font(.system(.title2, design: .monospaced).weight(.medium))
                    .multilineTextAlignment(.center)
                    .padding(Space.l)
                    .frame(minHeight: 64)
                    .dotGlass(in: RoundedRectangle(cornerRadius: Radius.l))
                    .onSubmit(pair)
                    .accessibilityIdentifier("pair-code")
                if let failure { NoticeCard(words: failure) }
                Button(action: pair) {
                    Text(working ? "Pairing…" : "Pair").frame(maxWidth: .infinity)
                }
                .dotButton(.prominent)
                .controlSize(.large)
                .disabled(code.trimmingCharacters(in: .whitespaces).isEmpty || working)
                HStack {
                    Button("Pair in the web UI instead") { model.showsWeb = true }
                    Spacer()
                    Button("Change address") { model.showsSettings = true }
                }
                .font(Theme.meta.weight(.medium)).foregroundStyle(Theme.muted)
                .padding(.horizontal, Space.xs)
            }
            .padding(.horizontal, Space.xl)
        }
        .scrollDismissesKeyboard(.interactively)
        .background {
            ZStack(alignment: .top) {
                DotBackground(glow: 0.6)
                DotMesh().frame(height: 460).mask(LinearGradient(colors: [.black, .clear], startPoint: .top, endPoint: .bottom)).ignoresSafeArea()
            }
        }
    }

    private func step(_ number: Int, _ words: String) -> some View {
        HStack(spacing: Space.m) {
            Text("\(number)").font(.footnote.weight(.bold).monospacedDigit()).foregroundStyle(Theme.accent)
                .frame(width: 26, height: 26).background(Theme.accent.opacity(0.14), in: Circle())
            Text(words).font(.subheadline).foregroundStyle(Theme.text)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.l).padding(.vertical, Space.m)
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
