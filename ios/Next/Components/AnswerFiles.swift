import DotEngine
import PDFKit
import SwiftUI
import UIKit

/// A size in bytes in the phone's words (`2,4 MB`).
func fileSize(_ bytes: Int) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
}

/// The symbol of a kind of file.
func fileSymbol(_ kind: AnswerFileKind) -> String {
    switch kind {
    case .image: "photo"
    case .pdf: "doc.richtext"
    case .code: "chevron.left.forwardslash.chevron.right"
    case .text, .document: "doc.text"
    case .table: "tablecells"
    case .slides: "rectangle.on.rectangle"
    case .page: "globe"
    case .other: "doc"
    }
}

/// The largest image and the most PDF pages the phone shows; a larger file shows its facts and "Open on Mac".
let phoneImageBytes: Int = 30 * 1024 * 1024
let phonePdfPages: Int = 40

/// What the preview sheet shows: its content kept as a file on the phone, for Share and Save to Files.
enum FileContent {
    case image(UIImage, URL)
    case pdf(PDFDocument, URL)
    /// A text read by its format (`fileTextFormat`): its text, for Copy and the Markdown source, and what is drawn of it.
    case text(String, cut: Bool, format: FileTextFormat, FileTextBody, URL)
    /// A CSV or TSV file's rows.
    case table(FileTable, URL)
    /// The facts alone, and why nothing more shows.
    case facts(String)
}

enum PreviewState {
    case loading
    case failed(String)
    case ready(ShownFile, FileContent)
}

/// A file's preview in a sheet (backlog LF1): an image zoomable, a PDF's pages, a text by its format (Markdown drawn as
/// the chat draws an answer with its source a tap away, JSON laid out, code coloured and never wrapped, plain text
/// selectable), a CSV or TSV file as a grid; a file too large or of a kind the phone does not show, its facts. Open on
/// Mac, Share and Save to Files float at the bottom, and every page's end scrolls clear of them; a swipe down closes it.
struct FilePreviewSheet: View {
    let path: String
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: PreviewState = .loading
    @State private var saving: URL?
    @State private var notice: (tone: NoticeTone, words: String)?
    @State private var opening: Bool = false
    @State private var showsSource: Bool = false

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Tone.sheet)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                    }
                    if case .ready(_, .text(_, _, .markdown, _, _)) = state {
                        ToolbarItem(placement: .principal) { markdownPicker }
                    }
                    if case .ready(let file, let shown) = state { actions(file, shown) }
                }
                .safeAreaInset(edge: .top) {
                    if let notice {
                        Notice(tone: notice.tone, message: notice.words, dismiss: { self.notice = nil })
                            .padding(.horizontal, Space.gutter).padding(.top, Space.xs)
                    }
                }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
        .task(id: path) { state = await model.filePreview(path) }
        .sheet(item: Binding(get: { saving.map { OpenedFile(path: $0.path) } }, set: { if $0 == nil { saving = nil } })) { _ in
            if let saving { SaveToFiles(url: saving).ignoresSafeArea() }
        }
    }

    private var title: String {
        if case .ready(let file, _) = state { return file.name }
        return (path as NSString).lastPathComponent
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .loading:
            ProgressView().controlSize(.regular).accessibilityLabel(String(localized: "Loading the file", comment: "VoiceOver: a file preview is being fetched"))
        case .failed(let words):
            EmptyState(title: String(localized: "The file cannot be shown", comment: "File preview: the Mac did not give the file"), systemImage: "doc.questionmark", detail: words, actionTitle: nil, action: {})
        case .ready(let file, .image(let image, _)):
            ZoomableImage(image: image, label: file.name).ignoresSafeArea(edges: .bottom)
        case .ready(_, .pdf(let document, _)):
            PDFPages(document: document).ignoresSafeArea(edges: .bottom)
        case .ready(_, .text(let text, let cut, let format, let body, _)):
            VStack(spacing: 0) {
                textBody(text, format: format, body: body)
                if cut {
                    Text(String(localized: "Only the start of the file shows here; open it on the Mac for the rest.", comment: "File preview: the text was cut"))
                        .textRole(.footnote).foregroundStyle(Tone.tertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Space.gutter).padding(.vertical, Space.xs)
                }
            }
            .contentMargins(.bottom, floatingClearance, for: .scrollContent)
        case .ready(_, .table(let table, _)):
            FileTableView(table: table)
                .contentMargins(.bottom, floatingClearance, for: .scrollContent)
        case .ready(let file, .facts(let why)):
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    InspectorSection(header: nil, footer: why.isEmpty ? nil : why) {
                        FactRow(title: String(localized: "Size", comment: "File preview row: the file's size"), value: fileSize(file.size))
                        FactRow(
                            title: String(localized: "Changed", comment: "File preview row: when the file last changed"),
                            value: Date(timeIntervalSince1970: file.modified).formatted(date: .abbreviated, time: .shortened))
                        FactRow(title: String(localized: "Where", comment: "File preview row: the file's folder on the Mac"), value: (file.path as NSString).deletingLastPathComponent)
                    }
                    if file.opens {
                        Button {
                            openOnMac(file)
                        } label: {
                            Label(String(localized: "Open on Mac", comment: "File preview: opens the file in its application on the Mac"), systemImage: "desktopcomputer")
                                .frame(maxWidth: .infinity, minHeight: Metric.hit)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(opening)
                    }
                }
                .padding(Space.gutter)
                .readableColumn()
            }
            .contentMargins(.bottom, floatingClearance, for: .scrollContent)
            .accessibilityIdentifier("file.facts")
        }
    }

    /// A text's body by its format; a Markdown file's source, when asked for, as its lines of code.
    @ViewBuilder private func textBody(_ text: String, format: FileTextFormat, body: FileTextBody) -> some View {
        switch body {
        case .markdown(let blocks) where !showsSource:
            MarkdownDocument(blocks: blocks)
        case .markdown:
            CodeLinesView(lines: codeLines(text: text, language: .markdown))
        case .code(let lines):
            CodeLinesView(lines: lines)
        case .plain(let pieces):
            PlainTextDocument(pieces: pieces)
        }
    }

    private var markdownPicker: some View {
        Picker(String(localized: "View", comment: "Change sheet: how a Markdown file is shown"), selection: $showsSource) {
            Text(String(localized: "Formatted", comment: "Change sheet: a Markdown file shown formatted")).tag(false)
            Text(String(localized: "Source", comment: "Change sheet: a Markdown file shown as it is written")).tag(true)
        }
        .pickerStyle(.segmented)
        .fixedSize()
        .accessibilityIdentifier("file.view")
    }

    /// How far a page's end scrolls above the bottom edge: clear of Open on Mac and the Share and Save pill that float
    /// over it (the owner's shot of 2026-11-28 had the last lines under them).
    private var floatingClearance: CGFloat { Metric.hit + Space.l }

    @ToolbarContentBuilder private func actions(_ file: ShownFile, _ shown: FileContent) -> some ToolbarContent {
        ToolbarItemGroup(placement: .bottomBar) {
            if file.opens {
                Button(String(localized: "Open on Mac", comment: "File preview: opens the file in its application on the Mac"), systemImage: "desktopcomputer") { openOnMac(file) }
                    .disabled(opening)
                    .accessibilityIdentifier("file.openOnMac")
            }
            Spacer()
            if let local: URL = localFile(shown) {
                ShareLink(item: local) {
                    Label(String(localized: "Share", comment: "Long press on a link or a file preview: the share sheet"), systemImage: "square.and.arrow.up")
                }
                Button(String(localized: "Save to Files", comment: "File preview: saves a copy in the Files app"), systemImage: "folder") { saving = local }
            }
        }
    }

    private func openOnMac(_ file: ShownFile) {
        opening = true
        Task {
            let failure: String? = await model.openOnMac(file.path)
            opening = false
            notice =
                failure.map { (.failure, $0) }
                ?? (.info, String(localized: "Opened on your Mac.", comment: "File preview: the file opened in its application on the Mac"))
        }
    }

    private func localFile(_ shown: FileContent) -> URL? {
        switch shown {
        case .image(_, let url), .pdf(_, let url), .text(_, _, _, _, let url), .table(_, let url): url
        case .facts: nil
        }
    }
}

/// A PDF's pages in PDFKit's own view: zoomable, one under another.
struct PDFPages: UIViewRepresentable {
    let document: PDFDocument

    func makeUIView(context: Context) -> PDFView {
        let view: PDFView = PDFView()
        view.document = document
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.backgroundColor = .secondarySystemBackground
        view.accessibilityIdentifier = "file.pdf"
        return view
    }

    func updateUIView(_ view: PDFView, context: Context) {}
}

/// The system's "Save to Files" for a copy of a file.
struct SaveToFiles: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        UIDocumentPickerViewController(forExporting: [url], asCopy: true)
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
}

// MARK: Reading the Mac's files

/// The Mac's facts of the files the sheets showed, for a minute, so a sheet opened again asks once.
@MainActor private var heldFacts: [String: (at: Date, file: ShownFile)] = [:]

extension AppModel {
    /// What the preview sheet shows of the file at `path`: its content fetched and kept as a file on the phone.
    func filePreview(_ path: String) async -> PreviewState {
        let file: ShownFile
        switch await shownFile(path) {
        case .success(let shown): file = shown
        case .failure(let words): return .failed(words.words)
        }
        guard let api: DaemonAPI = settingsAPI else { return .failed(fileFailureWords(.transport(path: path, underlying: URLError(.notConnectedToInternet)))) }
        do throws(DaemonRequestError) {
            switch file.preview {
            case .image:
                guard file.size <= phoneImageBytes else { return .ready(file, .facts(tooLargeWords)) }
                let data: Data = try await api.fileImage(path: file.path)
                guard let image: UIImage = UIImage(data: data) else {
                    return .ready(file, .facts(String(localized: "The phone cannot draw this image.", comment: "File preview: an image of a kind iOS does not draw")))
                }
                return .ready(file, .image(image, try keptFile(data, named: file.name)))
            case .pdf(let pages, let widths):
                guard pages.count <= phonePdfPages, let width: Int = phonePageWidth(widths) else { return .ready(file, .facts(tooLargeWords)) }
                let document: PDFDocument = PDFDocument()
                for (index, size) in pages.enumerated() {
                    let png: Data = try await api.filePage(path: file.path, number: index + 1, width: width)
                    guard let image: UIImage = UIImage(data: png),
                        let page: PDFPage = PDFPage(image: image, options: [.mediaBox: CGRect(x: 0, y: 0, width: size.width, height: size.height), .upscaleIfSmaller: true])
                    else { continue }
                    document.insert(page, at: document.pageCount)
                }
                guard let data: Data = document.dataRepresentation() else { return .ready(file, .facts("")) }
                return .ready(file, .pdf(document, try keptFile(data, named: file.name)))
            case .text(let text, let cut):
                let format: FileTextFormat = fileTextFormat(name: file.name)
                let body: FileTextBody = await Task.detached(priority: .userInitiated) { fileTextBody(text, format: format) }.value
                return .ready(file, .text(text, cut: cut, format: format, body, try keptFile(Data(text.utf8), named: file.name)))
            case .table(let table):
                let delimiter: Character = file.name.lowercased().hasSuffix(".tsv") ? "\t" : ","
                let written: String = await Task.detached(priority: .userInitiated) { fileTableText(table.rows, delimiter: delimiter) }.value
                return .ready(file, .table(table, try keptFile(Data(written.utf8), named: file.name)))
            case .drawn:
                return .ready(file, .facts(String(localized: "The phone shows this kind of file by its facts; open it on the Mac to read it.", comment: "File preview: an Office file")))
            case .unshown(let reason):
                return .ready(file, .facts(reason))
            }
        } catch {
            answersLogger.warning("a file could not be shown path=\(path, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return .failed(fileFailureWords(error))
        }
    }

    /// Opens the file at `path` in its application on the Mac: nil when it opened, else why not.
    func openOnMac(_ path: String) async -> String? {
        guard let api: DaemonAPI = settingsAPI else { return fileFailureWords(.transport(path: path, underlying: URLError(.notConnectedToInternet))) }
        do throws(DaemonRequestError) {
            try await api.openFileOnMac(path: path)
            return nil
        } catch {
            answersLogger.warning("a file did not open on the Mac path=\(path, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return fileFailureWords(error)
        }
    }

    private func shownFile(_ path: String) async -> Result<ShownFile, FileFailure> {
        if let held = heldFacts[path], held.at.timeIntervalSinceNow > -60 { return .success(held.file) }
        guard let api: DaemonAPI = settingsAPI else { return .failure(FileFailure(words: fileFailureWords(.transport(path: path, underlying: URLError(.notConnectedToInternet))))) }
        do throws(DaemonRequestError) {
            let file: ShownFile = try await api.shownFile(path: path)
            heldFacts[path] = (Date(), file)
            return .success(file)
        } catch {
            answersLogger.info("a file's facts were not read path=\(path, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return .failure(FileFailure(words: fileFailureWords(error)))
        }
    }

    private var tooLargeWords: String {
        String(localized: "The file is too large for the phone; open it on the Mac.", comment: "File preview: an image or a PDF too large to show")
    }
}

struct FileFailure: Error {
    let words: String
}

/// Why a file is not shown, in the phone's words: no such file on the Mac, a place the Mac does not show, or the
/// connection's words.
func fileFailureWords(_ error: DaemonRequestError) -> String {
    switch error.statusCode {
    case 404: String(localized: "Not on your Mac", comment: "File chip: the file the answer names is not on the Mac")
    case 422: error.detail.isEmpty ? String(localized: "Not shown from this place", comment: "File chip: the Mac does not show files of this folder") : error.detail
    default: failureWords(settingsFailure(error), macPlace: String(localized: "the chat's file panel", comment: "Where on the Mac a file of an answer is shown"))
    }
}

/// `data` kept as a file named `name` in a folder of its own in the phone's temporary files, for Share and Save to
/// Files.
private func keptFile(_ data: Data, named name: String) throws(DaemonRequestError) -> URL {
    let folder: URL = URL.temporaryDirectory.appending(path: "DotFiles/\(UUID().uuidString)", directoryHint: .isDirectory)
    let file: URL = folder.appending(path: name.isEmpty ? "file" : name)
    do {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try data.write(to: file, options: .atomic)
    } catch {
        throw .undecodable(path: file.path, detail: "the phone could not keep the file: \(error)")
    }
    return file
}
