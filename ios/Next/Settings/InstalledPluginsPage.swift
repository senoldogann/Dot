import DotEngine
import SwiftUI

private struct PluginRemoval: Sendable {
    let name: String
    let label: String
    let connectedApps: Bool
}

private enum PluginAction: Sendable {
    case revert(GalleryPlugin)
    case remove(PluginRemoval)

    var label: String {
        switch self {
        case .revert(let plugin): plugin.name
        case .remove(let removal): removal.label
        }
    }
}

struct InstalledPluginsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<InstalledPlugins> = .loading
    @State private var pending: PluginAction?
    @State private var busy: Bool = false
    @State private var problem: SettingsFailure?

    private var macPlace: String { String(localized: "Settings › Plugins", comment: "Where on the Mac: installed plugins") }

    var body: some View {
        SettingsScroll(title: String(localized: "Installed plugins", comment: "Settings: installed plugins and their versions")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { gallery in
                let installed = gallery.plugins.filter(\.installed)
                if installed.isEmpty && gallery.others.isEmpty {
                    GroupedSection(header: nil, footer: nil) {
                        ListRow(systemImage: "puzzlepiece", title: String(localized: "No installed plugins", comment: "Plugins: the list is empty"),
                                description: nil, accessory: .none)
                    }
                }
                ForEach(installed) { plugin in
                    GroupedSection(header: plugin.name, footer: plugin.does) {
                        if plugin.revertible {
                            Button { pending = .revert(plugin) } label: {
                                ListRow(systemImage: "arrow.uturn.backward", title: String(localized: "Revert", comment: "Plugins: restores the earlier version"),
                                        description: nil, accessory: .none)
                            }
                            .buttonStyle(RowPressStyle())
                        }
                        removeRow(PluginRemoval(name: plugin.id, label: plugin.name, connectedApps: false))
                    }
                }
                ForEach(gallery.others) { plugin in
                    GroupedSection(header: plugin.name, footer: plugin.description) {
                        if plugin.own {
                            removeRow(PluginRemoval(name: plugin.name, label: plugin.name, connectedApps: plugin.servers > 0))
                        } else {
                            ListRow(systemImage: "desktopcomputer", title: String(localized: "Managed on your Mac", comment: "Plugins: set up in the Mac's configuration"),
                                    description: nil, accessory: .none)
                        }
                    }
                }
            }
        }
        .disabled(busy)
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog(
            dialogTitle, isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
            titleVisibility: .visible, presenting: pending
        ) { action in
            switch action {
            case .revert(let plugin):
                Button(String(localized: "Revert", comment: "Plugins: restores the earlier version")) { Task { await revert(plugin) } }
            case .remove(let removal):
                Button(String(localized: "Remove", comment: "Plugins: sends the installed plugin to the Mac's Trash"), role: .destructive) {
                    Task { await remove(removal) }
                }
            }
        } message: { action in
            switch action {
            case .revert:
                Text(String(localized: "The updated version goes to the Trash.", comment: "Plugins: explains reverting"))
            case .remove(let removal):
                if removal.connectedApps {
                    Text(String(localized: "Its connected apps stop now and its skills leave new tasks.", comment: "Plugins: explains removing a connected plugin"))
                } else {
                    Text(String(localized: "Its skills leave new tasks.", comment: "Plugins: explains removing a plugin"))
                }
            }
        }
    }

    private var dialogTitle: String {
        switch pending {
        case .revert(let plugin):
            String(localized: "Put back the earlier version of \(plugin.name)?", comment: "Plugins: confirmation of a revert; the argument is the plugin name")
        case .remove(let removal):
            String(localized: "Remove \(removal.label) from Dot?", comment: "Plugins: confirmation of removal; the argument is the plugin name")
        case nil:
            String(localized: "Installed plugins", comment: "Settings: installed plugins and their versions")
        }
    }

    private func removeRow(_ removal: PluginRemoval) -> some View {
        Button { pending = .remove(removal) } label: {
            ListRow(systemImage: "trash", title: String(localized: "Remove", comment: "Plugins: sends the installed plugin to the Mac's Trash"),
                    description: nil, accessory: .none)
        }
        .buttonStyle(RowPressStyle())
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.installedPlugins() }
    }

    private func revert(_ plugin: GalleryPlugin) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.revertInstalledPlugin(plugin.id) }) {
        case .success(let latest): state = .loaded(latest); problem = nil
        case .failure(let box): problem = box.failure
        }
    }

    private func remove(_ removal: PluginRemoval) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.removeInstalledPlugin(removal.name) }) {
        case .success: problem = nil; await load()
        case .failure(let box): problem = box.failure
        }
    }
}
