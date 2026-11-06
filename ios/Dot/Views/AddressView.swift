import SwiftUI

/// Asks for the daemon's tailnet address and saves it once it parses.
struct AddressView: View {
    let model: AppModel
    /// Called after a save, e.g. to close the settings sheet; `nil` on the first-launch screen.
    let onSaved: (() -> Void)?
    @State private var input: String = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            DotGlyph(size: 48)
            Text("Your Mac's address")
                .font(.title2.weight(.semibold))
            Text("The tailnet address of the Mac Dot runs on, as Tailscale shows it, for example mac.your-tailnet.ts.net.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            TextField("mac.your-tailnet.ts.net", text: $input)
                .textContentType(.URL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .onSubmit(save)
                .padding(12)
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
            Button(action: save) {
                Text("Open Dot").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(input.trimmingCharacters(in: .whitespaces).isEmpty)
            Spacer()
        }
        .padding(24)
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
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
