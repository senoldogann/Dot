import SwiftUI

struct CodingWorkSnapshot: Decodable, Sendable {
    let worktree: String
    let branch: String
    let head: String
    let base: String
    let diffStat: String
    let prURL: URL?
    let prState: String?
    let capturedAt: Double

    enum CodingKeys: String, CodingKey {
        case worktree, branch, head, base
        case diffStat = "diff_stat"
        case prURL = "pr_url"
        case prState = "pr_state"
        case capturedAt = "captured_at"
    }
}

struct CodingWorkCard: View {
    let snapshot: CodingWorkSnapshot

    var body: some View {
        CardGroup(title: "Coding worktree") {
            VStack(alignment: .leading, spacing: Space.s) {
                Text("\(snapshot.branch) · \(snapshot.head.prefix(12))")
                    .font(Theme.headline)
                Text(snapshot.worktree).foregroundStyle(Theme.muted)
                if let url = snapshot.prURL, url.scheme == "https", url.host == "github.com" {
                    Link("Pull request", destination: url)
                    Link("Files changed", destination: url.appending(path: "files"))
                    if let state = snapshot.prState {
                        Text(state == "unverified" ? "Status unverified" : state.capitalized)
                            .foregroundStyle(Theme.muted)
                    }
                }
                DisclosureGroup("Committed changes from \(snapshot.base.prefix(12))") {
                    Text(snapshot.diffStat.isEmpty ? "No committed changes" : snapshot.diffStat)
                        .monospaced()
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Text("Captured \(Date(timeIntervalSince1970: snapshot.capturedAt).formatted())")
                    .foregroundStyle(Theme.muted)
            }
            .font(Theme.small)
            .padding(Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
