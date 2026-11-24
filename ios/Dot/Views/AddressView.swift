import SwiftUI

/// Asks for the daemon's tailnet address and saves it once it parses.
struct AddressView: View {
    let model: AppModel
    /// Called after a save, e.g. to close the settings sheet; `nil` on the first-launch screen.
    let onSaved: (() -> Void)?
    @State private var input: String = ""
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.l) {
                VStack(spacing: Space.m) {
                    DotGlyph(size: 72)
                    Text("Your Mac's address").font(.title.weight(.semibold)).foregroundStyle(Theme.text)
                    Text("The tailnet address of the Mac Dot runs on, as Tailscale shows it, for example mac.your-tailnet.ts.net.")
                        .font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.xxl)
                TextField("", text: $input, prompt: Text("mac.your-tailnet.ts.net").foregroundStyle(Theme.muted))
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .onSubmit(save)
                    .font(.body)
                    .padding(Space.l)
                    .frame(minHeight: 56)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Radius.l))
                    .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.hairline, lineWidth: 1))
                if let error { NoticeCard(words: error) }
                Button(action: save) {
                    Text("Open Dot").frame(maxWidth: .infinity)
                }
                .dotButton(.primary)
                .controlSize(.large)
                .disabled(input.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(.horizontal, Space.xl)
        }
        .scrollDismissesKeyboard(.interactively)
        .background { DotBackground() }
        .onAppear {
            input = model.host?.name ?? model.buildHost ?? ""
        }
    }

    private func save() {
        switch parseDaemonHost(input, buildHost: model.buildHost) {
        case .success(let host):
            error = nil
            model.setHost(host)
            onSaved?()
        case .failure(let failure):
            error = failure.description
        }
    }
}

/// The address, the pairing state and a way to forget the key; opened with a two-finger long press on the web UI or
/// from the error screen.
struct SettingsView: View {
    let model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    AddressView(model: model, onSaved: { dismiss() })
                        .listRowInsets(EdgeInsets())
                }
                Section("Phone") {
                    LabeledContent("Paired", value: model.isPaired ? "Yes" : "Not yet")
                    if model.isPaired {
                        Button("Forget this phone's key", role: .destructive) {
                            model.forgetKey()
                        }
                    }
                }
            }
            .navigationTitle("Dot")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                DoneButton { dismiss() }
            }
        }
    }
}
