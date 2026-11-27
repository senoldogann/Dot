import AVKit
import DotEngine
import SwiftUI
import UIKit
import os

private let playerLogger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "player")

/// The first frame and the length of a video a message carried.
struct VideoPreview {
    let frame: UIImage
    let duration: Double?
}

/// A ``VideoPreview`` for an `NSCache`, which keeps objects and may let them go under memory pressure.
final class VideoPreviewBox {
    let preview: VideoPreview
    init(_ preview: VideoPreview) { self.preview = preview }
}

/// The length of a video as a tile says it: "0:14", "12:03".
func durationWords(_ seconds: Double) -> String {
    Duration.seconds(seconds.rounded()).formatted(.time(pattern: .minuteSecond))
}

/// One tile of a photo, a video or a sound: its picture (the video's first frame), the play glyph over a video, its length
/// at the foot, and while it is being made ready a ring that fills. The composer and the chat draw the same tile, so a
/// video looks as it did when it was added (item 1992).
struct MediaTile: View {
    let preview: UIImage?
    let duration: Double?
    let playable: Bool
    /// The glyph while there is no picture yet.
    let icon: String
    /// 0 to 1 while the file is being prepared or sent; `nil` when it is ready.
    let progress: Double?
    @ScaledMetric(relativeTo: .body) private var side: CGFloat = 96

    var body: some View {
        ZStack {
            Tone.nested
            if let preview {
                Image(uiImage: preview).resizable().scaledToFill()
            } else {
                Image(systemName: icon).foregroundStyle(Tone.secondary).imageScale(.large).accessibilityHidden(true)
            }
            if playable && progress == nil {
                Image(systemName: "play.fill")
                    .font(.system(.callout, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(.black.opacity(0.5), in: Circle())
                    .accessibilityHidden(true)
            }
            if let progress {
                ProgressView(value: min(1, max(0, progress)))
                    .progressViewStyle(.circular)
                    .tint(.white)
                    .frame(width: 36, height: 36)
                    .padding(Space.xs)
                    .background(.black.opacity(0.5), in: Circle())
                    .accessibilityIdentifier("tile.progress")
            }
        }
        .frame(width: side, height: side)
        .overlay(alignment: .bottomLeading) {
            if let duration {
                Text(verbatim: durationWords(duration))
                    .textRole(.label).foregroundStyle(.white).monospacedDigit()
                    .padding(.horizontal, Space.xs).padding(.vertical, Space.xxs)
                    .background(.black.opacity(0.5), in: Capsule())
                    .padding(Space.xs)
                    .accessibilityIdentifier("tile.duration")
            }
        }
        .clipShape(rounded(Radius.inner))
        .contentShape(rounded(Radius.inner))
    }
}

/// What the owner's message carried, above its words (item 1992): a photo as its picture, a video as a tile that plays
/// full screen on a tap, a sound or any other file as a chip with its name and size.
struct OwnerFilesView: View {
    let files: [AttachedFile]

    var body: some View {
        let tiles: [AttachedFile] = files.filter { $0.kind == "image" || $0.kind == "video" }
        let chips: [AttachedFile] = files.filter { $0.kind != "image" && $0.kind != "video" }
        VStack(alignment: .trailing, spacing: Space.xxs) {
            if !tiles.isEmpty {
                // Up to ten of them: in a row when they fit, else a row that scrolls.
                let row = HStack(spacing: Space.xxs) {
                    ForEach(tiles, id: \.id) { file in
                        if file.kind == "video" { OwnerVideoTile(file: file) } else { OwnerPictureTile(file: file) }
                    }
                }
                ViewThatFits(in: .horizontal) {
                    row
                    ScrollView(.horizontal) { row }.scrollIndicators(.hidden).defaultScrollAnchor(.trailing)
                }
            }
            ForEach(chips, id: \.id) { OwnerFileChip(file: $0) }
        }
    }
}

private struct OwnerPictureTile: View {
    let file: AttachedFile
    @Environment(AppModel.self) private var model: AppModel
    @State private var picture: UIImage?

    var body: some View {
        MediaTile(preview: picture, duration: nil, playable: false, icon: "photo", progress: nil)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(String(localized: "Photo \(file.name)", comment: "VoiceOver: a photo the owner's message carried; the argument is its name"))
            .accessibilityIdentifier("owner.file.photo")
            .task(id: file.id) { picture = await model.attachmentPicture(file.id) }
    }
}

private struct OwnerVideoTile: View {
    let file: AttachedFile
    @Environment(AppModel.self) private var model: AppModel
    @State private var preview: VideoPreview?
    @State private var playing: Bool = false

    var body: some View {
        Button { playing = true } label: {
            MediaTile(preview: preview?.frame, duration: preview?.duration, playable: true, icon: "film", progress: nil)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(String(localized: "Play video \(file.name)", comment: "VoiceOver: a video the owner's message carried; the argument is its name"))
        .accessibilityIdentifier("owner.file.video")
        .task(id: file.id) { preview = await model.attachmentVideo(file.id) }
        .fullScreenCover(isPresented: $playing) {
            if let asset = model.attachmentAsset(file.id) {
                MediaPlayerScreen(asset: asset, close: { playing = false })
            }
        }
    }
}

private struct OwnerFileChip: View {
    let file: AttachedFile
    @Environment(AppModel.self) private var model: AppModel
    @State private var playing: Bool = false

    private var isSound: Bool { file.kind == "audio" }

    var body: some View {
        let chip = HStack(spacing: Space.xs) {
            Image(systemName: isSound ? "waveform" : "doc").foregroundStyle(Tone.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Text(verbatim: file.name).textRole(.footnote).lineLimit(1).truncationMode(.middle)
                Text(verbatim: Int64(file.size).formatted(.byteCount(style: .file))).textRole(.label).foregroundStyle(Tone.secondary)
            }
            .frame(maxWidth: 200, alignment: .leading)
        }
        .padding(.horizontal, Space.s)
        .padding(.vertical, Space.xs)
        .background(Tone.nested, in: rounded(Radius.inner))
        if isSound {
            Button { playing = true } label: { chip }
                .buttonStyle(.plain)
                .accessibilityIdentifier("owner.file.sound")
                .fullScreenCover(isPresented: $playing) {
                    if let asset = model.attachmentAsset(file.id) { MediaPlayerScreen(asset: asset, close: { playing = false }) }
                }
        } else {
            chip.accessibilityElement(children: .combine).accessibilityIdentifier("owner.file.chip")
        }
    }
}

/// A video or a sound played full screen with the system's controls, from the asset it is given: a file of the phone, or
/// the Mac's attachment route in ranges. It plays at once and stops when it is closed.
struct MediaPlayerScreen: View {
    let asset: AVURLAsset
    let close: () -> Void
    @State private var player: AVPlayer = AVPlayer()
    /// What the audio session was before the player took it (the dictation's, or the app's default): put back on close.
    @State private var before: AudioSessionState?

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player)
                .ignoresSafeArea()
                .accessibilityIdentifier("player.video")
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(.body, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: Metric.hit, height: Metric.hit)
                    .background(.black.opacity(0.5), in: Circle())
                    .padding(Space.s)
            }
            .accessibilityLabel(String(localized: "Close", comment: "VoiceOver: closes the full-screen video"))
            .accessibilityIdentifier("player.close")
        }
        .onAppear {
            // Played as a movie is: the Ring/Silent switch does not mute it.
            before = AudioSessionState.take(for: .playback)
            player.replaceCurrentItem(with: AVPlayerItem(asset: asset))
            player.play()
        }
        .onDisappear {
            player.pause()
            before?.restore()
        }
    }
}

/// The audio session as it was, for the player to put back.
struct AudioSessionState {
    let category: AVAudioSession.Category
    let mode: AVAudioSession.Mode
    let options: AVAudioSession.CategoryOptions

    /// Switches the session to ``category`` (movie playback) and answers what it was; `nil` when it could not be switched.
    static func take(for category: AVAudioSession.Category) -> AudioSessionState? {
        let session: AVAudioSession = AVAudioSession.sharedInstance()
        let was: AudioSessionState = AudioSessionState(category: session.category, mode: session.mode, options: session.categoryOptions)
        do {
            try session.setCategory(category, mode: .moviePlayback, options: [])
            try session.setActive(true)
            return was
        } catch {
            playerLogger.warning("the audio session could not be set for playback error=\(String(describing: error), privacy: .public)")
            return nil
        }
    }

    func restore() {
        let session: AVAudioSession = AVAudioSession.sharedInstance()
        do {
            try session.setActive(false, options: .notifyOthersOnDeactivation)
            try session.setCategory(category, mode: mode, options: options)
        } catch {
            playerLogger.warning("the audio session could not be put back error=\(String(describing: error), privacy: .public)")
        }
    }
}
