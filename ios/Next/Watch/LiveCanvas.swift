import AVFoundation
import CoreMedia
import DotEngine
import SwiftUI
import UIKit

// The live picture's stage (items 1838-1840): the Mac's screen decoded by the phone's own H.264 decoder, or a run's
// browser tab as JPEG frames, fitted to the stage, zoomed with two fingers and panned, and, while the owner holds the
// run, turned into the page's input (a tap clicks, a drag scrolls, a press held drags).

/// Draws H.264 frames of the live stream on its display layer. Fed by one reader at a time (its own task); the layer's
/// renderer takes sample buffers from any thread.
final class VideoPicture: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    var displayLayer: AVSampleBufferDisplayLayer {
        guard let layer = layer as? AVSampleBufferDisplayLayer else { preconditionFailure("the layer class is AVSampleBufferDisplayLayer") }
        return layer
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        displayLayer.videoGravity = .resizeAspect
        backgroundColor = .black
        isAccessibilityElement = false
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { preconditionFailure("not made from a storyboard") }
}

/// Why a frame could not be drawn: the stream's configuration or a frame is not one the decoder takes.
struct VideoFeedError: Error, CustomStringConvertible {
    let description: String
}

/// Turns the stream's frames into sample buffers for the layer: the decoder's format from each key frame's `avcC`, each
/// frame shown at once (no clock: the newest frame is the screen now). Used by one reader's task only.
final class H264Feeder: @unchecked Sendable {
    private let renderer: AVSampleBufferVideoRenderer
    private var format: CMVideoFormatDescription?
    private var record: Data = Data()

    init(renderer: AVSampleBufferVideoRenderer) {
        self.renderer = renderer
    }

    /// Draws `frame`; returns the picture's size in pixels when a key frame (re)configured the decoder.
    func feed(_ frame: LiveFrame) throws(VideoFeedError) -> CGSize? {
        var data: Data = frame.payload
        var configured: CGSize?
        if frame.kind == .key {
            guard let split = splitKeyPayload(frame.payload) else { throw VideoFeedError(description: "a key frame shorter than it says") }
            data = split.frame
            if split.avcc != record {
                format = try Self.format(of: split.avcc)
                record = split.avcc
                renderer.flush()
                if let format {
                    let dimensions: CMVideoDimensions = CMVideoFormatDescriptionGetDimensions(format)
                    configured = CGSize(width: Int(dimensions.width), height: Int(dimensions.height))
                }
            }
        }
        // A delta before the first key frame has nothing to be decoded against.
        guard let format else { return nil }
        if renderer.status == .failed {
            // A decoder that failed (the app was away) starts again at the next key frame.
            renderer.flush()
            guard frame.kind == .key else { return configured }
        }
        renderer.enqueue(try Self.sample(data, format: format))
        return configured
    }

    private static func format(of record: Data) throws(VideoFeedError) -> CMVideoFormatDescription {
        guard let configuration: AVCConfiguration = avcConfiguration(record) else { throw VideoFeedError(description: "an avcC record the app cannot read") }
        let sets: [Data] = configuration.sequenceSets + configuration.pictureSets
        let joined: Data = sets.reduce(Data(), +)
        var made: CMVideoFormatDescription?
        let status: OSStatus = joined.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> OSStatus in
            guard let base: UnsafePointer<UInt8> = raw.bindMemory(to: UInt8.self).baseAddress else { return -1 }
            var offset: Int = 0
            var pointers: [UnsafePointer<UInt8>] = []
            for set in sets {
                pointers.append(base + offset)
                offset += set.count
            }
            return CMVideoFormatDescriptionCreateFromH264ParameterSets(
                allocator: kCFAllocatorDefault, parameterSetCount: sets.count, parameterSetPointers: pointers, parameterSetSizes: sets.map(\.count),
                nalUnitHeaderLength: Int32(configuration.nalLengthSize), formatDescriptionOut: &made)
        }
        guard status == noErr, let made else { throw VideoFeedError(description: "the decoder refused the stream's configuration (\(status))") }
        return made
    }

    private static func sample(_ data: Data, format: CMVideoFormatDescription) throws(VideoFeedError) -> CMSampleBuffer {
        var block: CMBlockBuffer?
        var status: OSStatus = CMBlockBufferCreateWithMemoryBlock(
            allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: data.count, blockAllocator: kCFAllocatorDefault, customBlockSource: nil,
            offsetToData: 0, dataLength: data.count, flags: 0, blockBufferOut: &block)
        guard status == noErr, let block else { throw VideoFeedError(description: "no memory for a frame (\(status))") }
        status = data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> OSStatus in
            guard let base: UnsafeRawPointer = raw.baseAddress else { return -1 }
            return CMBlockBufferReplaceDataBytes(with: base, blockBuffer: block, offsetIntoDestination: 0, dataLength: data.count)
        }
        guard status == noErr else { throw VideoFeedError(description: "a frame could not be copied (\(status))") }
        var sample: CMSampleBuffer?
        var size: Int = data.count
        status = CMSampleBufferCreateReady(
            allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format, sampleCount: 1, sampleTimingEntryCount: 0, sampleTimingArray: nil,
            sampleSizeEntryCount: 1, sampleSizeArray: &size, sampleBufferOut: &sample)
        guard status == noErr, let sample else { throw VideoFeedError(description: "a frame could not be made a sample (\(status))") }
        if let attachments: CFArray = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true), CFArrayGetCount(attachments) > 0 {
            let first: CFMutableDictionary = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(
                first, Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(), Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
        }
        return sample
    }
}

/// What the owner's touches on a held run's picture become (`InputPipe` sends them).
@MainActor
protocol CanvasControl: AnyObject {
    func tap(at place: PictureFraction, clicks: Int)
    func scroll(by points: CGVector, at place: PictureFraction, drawnWidth: CGFloat)
    func press(_ phase: UIGestureRecognizer.State, at place: PictureFraction)
}

/// A scroll view that fits its picture, zooms it from 1x to 5x (two fingers, or a double tap while only watched), keeps
/// it centred, and while `control` is set hands one finger's taps, drags and presses to it.
final class LiveCanvas: UIScrollView, UIScrollViewDelegate {
    let picture: UIView
    /// The picture's own size in pixels: the drawn rect keeps its aspect.
    var pictureSize: CGSize = CGSize(width: 16, height: 10) {
        didSet { if pictureSize != oldValue { setNeedsLayout() } }
    }
    weak var control: (any CanvasControl)? {
        didSet { controlChanged() }
    }
    /// A tap while only watched (the chrome shows or hides).
    var onWatchTap: (() -> Void)?

    private var fitted: CGSize = .zero
    private var lastTap: (at: Date, point: CGPoint, clicks: Int)?
    private let watchTap: UITapGestureRecognizer = UITapGestureRecognizer()
    private let zoomTap: UITapGestureRecognizer = UITapGestureRecognizer()
    private let controlTap: UITapGestureRecognizer = UITapGestureRecognizer()
    private let controlDrag: UIPanGestureRecognizer = UIPanGestureRecognizer()
    private let controlPress: UILongPressGestureRecognizer = UILongPressGestureRecognizer()

    init(picture: UIView) {
        self.picture = picture
        super.init(frame: .zero)
        delegate = self
        minimumZoomScale = 1
        maximumZoomScale = 5
        bouncesZoom = true
        showsVerticalScrollIndicator = false
        showsHorizontalScrollIndicator = false
        contentInsetAdjustmentBehavior = .never
        decelerationRate = .fast
        backgroundColor = .black
        addSubview(picture)
        zoomTap.numberOfTapsRequired = 2
        zoomTap.addTarget(self, action: #selector(zoomTapped(_:)))
        watchTap.require(toFail: zoomTap)
        watchTap.addTarget(self, action: #selector(watchTapped))
        controlTap.addTarget(self, action: #selector(controlTapped(_:)))
        controlDrag.maximumNumberOfTouches = 1
        controlDrag.addTarget(self, action: #selector(controlDragged(_:)))
        controlPress.minimumPressDuration = 0.45
        controlPress.addTarget(self, action: #selector(controlPressed(_:)))
        controlDrag.require(toFail: controlPress)
        for recognizer in [watchTap, zoomTap, controlTap, controlDrag, controlPress] { picture.addGestureRecognizer(recognizer) }
        picture.isUserInteractionEnabled = true
        controlChanged()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { preconditionFailure("not made from a storyboard") }

    private func controlChanged() {
        let controlling: Bool = control != nil
        watchTap.isEnabled = !controlling
        zoomTap.isEnabled = !controlling
        controlTap.isEnabled = controlling
        controlDrag.isEnabled = controlling
        controlPress.isEnabled = controlling
        // While the page is used, one finger is the page's: the picture moves with two.
        panGestureRecognizer.minimumNumberOfTouches = controlling ? 2 : 1
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, bounds.height > 0, pictureSize.width > 0, pictureSize.height > 0 else { return }
        let scale: CGFloat = min(bounds.width / pictureSize.width, bounds.height / pictureSize.height)
        let size: CGSize = CGSize(width: (pictureSize.width * scale).rounded(), height: (pictureSize.height * scale).rounded())
        if size != fitted {
            fitted = size
            let zoom: CGFloat = zoomScale
            zoomScale = 1
            picture.frame = CGRect(origin: .zero, size: size)
            contentSize = size
            zoomScale = zoom
        }
        centre()
    }

    private func centre() {
        let horizontal: CGFloat = max(0, (bounds.width - contentSize.width) / 2)
        let vertical: CGFloat = max(0, (bounds.height - contentSize.height) / 2)
        contentInset = UIEdgeInsets(top: vertical, left: horizontal, bottom: vertical, right: horizontal)
    }

    func viewForZooming(in scrollView: UIScrollView) -> UIView? { picture }
    func scrollViewDidZoom(_ scrollView: UIScrollView) { centre() }

    /// Back to the whole picture.
    func fit(animated: Bool) { setZoomScale(1, animated: animated) }

    private func fraction(_ point: CGPoint) -> PictureFraction? {
        let size: CGSize = picture.bounds.size
        guard size.width > 0, size.height > 0, picture.bounds.contains(point) else { return nil }
        return PictureFraction(x: Double(point.x / size.width), y: Double(point.y / size.height))
    }

    private func clampedFraction(_ point: CGPoint) -> PictureFraction {
        let size: CGSize = picture.bounds.size
        return PictureFraction(x: Double(min(max(point.x / max(size.width, 1), 0), 1)), y: Double(min(max(point.y / max(size.height, 1), 0), 1)))
    }

    @objc private func watchTapped() { onWatchTap?() }

    @objc private func zoomTapped(_ recognizer: UITapGestureRecognizer) {
        if zoomScale > 1.01 {
            setZoomScale(1, animated: true)
        } else {
            let point: CGPoint = recognizer.location(in: picture)
            let scale: CGFloat = 2.5
            let size: CGSize = CGSize(width: bounds.width / scale, height: bounds.height / scale)
            zoom(to: CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2, width: size.width, height: size.height), animated: true)
        }
    }

    @objc private func controlTapped(_ recognizer: UITapGestureRecognizer) {
        let point: CGPoint = recognizer.location(in: picture)
        guard let place: PictureFraction = fraction(point) else { return }
        // A second tap soon after and near the first is a double click; the first already went as a click, as a mouse's
        // first click of a double does.
        let now: Date = .now
        var clicks: Int = 1
        if let lastTap, now.timeIntervalSince(lastTap.at) < 0.35, hypot(point.x - lastTap.point.x, point.y - lastTap.point.y) < 24 / zoomScale {
            clicks = min(lastTap.clicks + 1, 3)
        }
        lastTap = (now, point, clicks)
        control?.tap(at: place, clicks: clicks)
    }

    @objc private func controlDragged(_ recognizer: UIPanGestureRecognizer) {
        guard recognizer.state == .changed else { return }
        let moved: CGPoint = recognizer.translation(in: picture)
        recognizer.setTranslation(.zero, in: picture)
        let place: PictureFraction = clampedFraction(recognizer.location(in: picture))
        control?.scroll(by: CGVector(dx: moved.x, dy: moved.y), at: place, drawnWidth: picture.bounds.width)
    }

    @objc private func controlPressed(_ recognizer: UILongPressGestureRecognizer) {
        control?.press(recognizer.state, at: clampedFraction(recognizer.location(in: picture)))
    }
}

/// The canvas in SwiftUI: the picture view is the caller's (a `VideoPicture` or a `UIImageView`), kept across redraws.
struct LiveCanvasView: UIViewRepresentable {
    let picture: UIView
    let pictureSize: CGSize
    let control: (any CanvasControl)?
    let onWatchTap: () -> Void
    /// Changes to fit the picture again (a new run, the hold ended).
    let fitToken: Int

    final class Coordinator {
        var fitToken: Int = 0
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> LiveCanvas {
        let canvas: LiveCanvas = LiveCanvas(picture: picture)
        canvas.accessibilityLabel = String(localized: "Live picture", comment: "VoiceOver: the live picture of the Mac's screen or Dot's browser")
        canvas.accessibilityHint = String(localized: "Pinch to zoom, double-tap to zoom in", comment: "VoiceOver hint: how to zoom the live picture")
        return canvas
    }

    func updateUIView(_ canvas: LiveCanvas, context: Context) {
        canvas.pictureSize = pictureSize
        canvas.control = control
        canvas.onWatchTap = onWatchTap
        if context.coordinator.fitToken != fitToken {
            context.coordinator.fitToken = fitToken
            canvas.fit(animated: true)
        }
    }
}
