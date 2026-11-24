import SafariServices
import SwiftUI

/// Show a daemon-authored preview in Safari's isolated surface. The page never receives the native API key.
struct ShownPageRow: View {
    let title: String
    let url: URL
    @State private var showing: Bool = false

    var body: some View {
        Button { showing = true } label: {
            HStack {
                Image(systemName: "doc.richtext").accessibilityHidden(true)
                Text(title).font(Theme.body).frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "arrow.up.right").accessibilityHidden(true)
            }
            .padding(Space.m)
            .card()
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens an interactive preview")
        .sheet(isPresented: $showing) {
            ShownPageBrowser(url: url).ignoresSafeArea()
        }
    }
}

private struct ShownPageBrowser: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

extension EnvironmentValues {
    @Entry var shownPageHost: DaemonHost?
}
