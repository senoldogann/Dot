import AVFoundation
import CoreGraphics
import Foundation

/// What a video sent to Dot is made into (item 1933): H.264 at a target bitrate, not one of the system's presets, whose
/// size follows the picture (a 720p preset made 26 MB per 30 s: 7 Mbps). 1.8 Mbps of video and 80 kbps of AAC make a
/// minute about 14 MB, so the daemon's 200 MiB cap holds a quarter of an hour, and a screen recording's text stays
/// readable in the frames `watch_video` takes.
enum VideoTarget {
    static let videoBitsPerSecond: Int = 1_800_000
    static let audioBitsPerSecond: Int = 80_000
    static let longestSide: Int = 1280
    static let framesPerSecond: Double = 30
    static let keyFrameSeconds: Int = 2
    static let audioSampleRate: Double = 44_100

    /// The coded size of a picture of ``natural`` size: its longest side at most ``longestSide``, never larger than the
    /// source, both sides even (H.264 codes 2×2 blocks of chroma).
    static func size(of natural: CGSize) -> (width: Int, height: Int) {
        let longest: CGFloat = max(natural.width, natural.height)
        let scale: CGFloat = longest > CGFloat(longestSide) ? CGFloat(longestSide) / longest : 1
        return (even(natural.width * scale), even(natural.height * scale))
    }

    private static func even(_ value: CGFloat) -> Int { max(2, Int((value / 2).rounded()) * 2) }
}

enum VideoTranscodeError: Error, Equatable {
    case noVideo
    case cannotRead(String)
    case cannotWrite(String)
    case failed(String)
}

/// Re-encodes a movie with ``VideoTarget``. Frames above 30 a second are left out, the sound is kept as AAC, and the
/// rotation the phone recorded the movie with stays a flag of the file, not baked into the picture.
enum VideoTranscoder {
    /// ``progress`` is told how far the picture has got, 0 to 1, as it goes (from any thread).
    static func export(from source: URL, to destination: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        let asset: AVURLAsset = AVURLAsset(url: source)
        guard let track: AVAssetTrack = try await asset.loadTracks(withMediaType: .video).first else {
            throw VideoTranscodeError.noVideo
        }
        let natural: CGSize = try await track.load(.naturalSize)
        let transform: CGAffineTransform = try await track.load(.preferredTransform)
        let audio: AVAssetTrack? = try await asset.loadTracks(withMediaType: .audio).first
        let length: Double = try await asset.load(.duration).seconds
        let job: TranscodeJob = try TranscodeJob(
            asset: asset, video: track, audio: audio, size: VideoTarget.size(of: natural), transform: transform,
            destination: destination, length: length, progress: progress)
        try await withTaskCancellationHandler {
            try await job.run()
        } onCancel: {
            job.cancel()
        }
    }
}

/// One export under way. The reader and the writer are not ``Sendable``: every touch of them is made on ``queue``, which
/// is what makes this class safe to share.
private final class TranscodeJob: @unchecked Sendable {
    private let reader: AVAssetReader
    private let writer: AVAssetWriter
    private let videoOut: AVAssetReaderTrackOutput
    private let videoIn: AVAssetWriterInput
    private let audioPair: (out: AVAssetReaderTrackOutput, input: AVAssetWriterInput)?
    private let queue: DispatchQueue = DispatchQueue(label: "dev.dotagent.phone.next.transcode")
    private let destination: URL
    private let length: Double
    private let progress: @Sendable (Double) -> Void
    private var nextVideoTime: CMTime = .zero
    private var finished: Bool = false
    /// What ``run()`` waits on, resumed once: by the export's end, its failure or its cancellation.
    private var waiting: CheckedContinuation<Void, any Error>?

    init(asset: AVURLAsset, video: AVAssetTrack, audio: AVAssetTrack?, size: (width: Int, height: Int), transform: CGAffineTransform, destination: URL, length: Double, progress: @escaping @Sendable (Double) -> Void) throws {
        self.destination = destination
        self.length = length
        self.progress = progress
        try? FileManager.default.removeItem(at: destination)
        do { reader = try AVAssetReader(asset: asset) } catch { throw VideoTranscodeError.cannotRead(error.localizedDescription) }
        do { writer = try AVAssetWriter(outputURL: destination, fileType: .mp4) } catch { throw VideoTranscodeError.cannotWrite(error.localizedDescription) }
        let color: [String: String] = [
            AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
            AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
            AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
        ]
        // 8-bit 4:2:0 in BT.709: an HDR recording is tone-mapped by the reader, not written as washed-out SDR.
        videoOut = AVAssetReaderTrackOutput(
            track: video,
            outputSettings: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                AVVideoColorPropertiesKey: color,
            ])
        videoOut.alwaysCopiesSampleData = false
        videoIn = AVAssetWriterInput(
            mediaType: .video,
            outputSettings: [
                AVVideoCodecKey: AVVideoCodecType.h264,
                AVVideoWidthKey: size.width,
                AVVideoHeightKey: size.height,
                AVVideoScalingModeKey: AVVideoScalingModeResizeAspect,
                AVVideoColorPropertiesKey: color,
                AVVideoCompressionPropertiesKey: [
                    AVVideoAverageBitRateKey: VideoTarget.videoBitsPerSecond,
                    AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
                    AVVideoMaxKeyFrameIntervalDurationKey: VideoTarget.keyFrameSeconds,
                    AVVideoExpectedSourceFrameRateKey: Int(VideoTarget.framesPerSecond),
                ],
            ])
        videoIn.transform = transform
        videoIn.expectsMediaDataInRealTime = false
        guard reader.canAdd(videoOut), writer.canAdd(videoIn) else { throw VideoTranscodeError.cannotWrite("the video cannot be re-encoded") }
        reader.add(videoOut)
        writer.add(videoIn)
        if let audio {
            let out: AVAssetReaderTrackOutput = AVAssetReaderTrackOutput(
                track: audio,
                outputSettings: [
                    AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false,
                    AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false,
                ])
            let input: AVAssetWriterInput = AVAssetWriterInput(
                mediaType: .audio,
                outputSettings: [
                    AVFormatIDKey: kAudioFormatMPEG4AAC, AVNumberOfChannelsKey: 2,
                    AVSampleRateKey: VideoTarget.audioSampleRate, AVEncoderBitRateKey: VideoTarget.audioBitsPerSecond,
                ])
            input.expectsMediaDataInRealTime = false
            guard reader.canAdd(out), writer.canAdd(input) else { throw VideoTranscodeError.cannotWrite("the sound cannot be re-encoded") }
            reader.add(out)
            writer.add(input)
            audioPair = (out, input)
        } else {
            audioPair = nil
        }
        writer.shouldOptimizeForNetworkUse = true
    }

    func run() async throws {
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, any Error>) in
            queue.async { self.start(done) }
        }
    }

    func cancel() {
        queue.async {
            guard !self.finished else { return }
            self.finished = true
            self.reader.cancelReading()
            self.writer.cancelWriting()
            try? FileManager.default.removeItem(at: self.destination)
            // The export that began is ended for whoever waits on it; one not begun yet is ended by ``start``.
            self.waiting?.resume(throwing: CancellationError())
        }
    }

    private func start(_ done: CheckedContinuation<Void, any Error>) {
        guard !finished else {
            done.resume(throwing: CancellationError())
            return
        }
        waiting = done
        guard reader.startReading() else {
            finished = true
            done.resume(throwing: VideoTranscodeError.cannotRead(reader.error?.localizedDescription ?? "unknown"))
            return
        }
        guard writer.startWriting() else {
            finished = true
            reader.cancelReading()
            done.resume(throwing: VideoTranscodeError.cannotWrite(writer.error?.localizedDescription ?? "unknown"))
            return
        }
        writer.startSession(atSourceTime: .zero)
        let pending: PendingInputs = PendingInputs(count: audioPair == nil ? 1 : 2)
        videoIn.requestMediaDataWhenReady(on: queue) { [self] in
            drain(videoIn, from: videoOut, dropsFrames: true, pending: pending, done: done)
        }
        audioPair?.input.requestMediaDataWhenReady(on: queue) { [self] in
            guard let pair = audioPair else { return }
            drain(pair.input, from: pair.out, dropsFrames: false, pending: pending, done: done)
        }
    }

    /// Copies samples while the input takes them. Called on ``queue``.
    private func drain(_ input: AVAssetWriterInput, from output: AVAssetReaderTrackOutput, dropsFrames: Bool, pending: PendingInputs, done: CheckedContinuation<Void, any Error>) {
        guard !finished, !pending.closed(input) else { return }
        while input.isReadyForMoreMediaData {
            guard let sample: CMSampleBuffer = output.copyNextSampleBuffer() else {
                input.markAsFinished()
                pending.close(input)
                if pending.allClosed { complete(done) }
                return
            }
            if dropsFrames, length > 0 {
                progress(min(1, max(0, CMSampleBufferGetPresentationTimeStamp(sample).seconds / length)))
            }
            if dropsFrames {
                // A 60 fps recording spends half the bitrate on frames nobody reads: keep one in 30 a second.
                let time: CMTime = CMSampleBufferGetPresentationTimeStamp(sample)
                if time < nextVideoTime { continue }
                nextVideoTime = CMTimeAdd(time, CMTime(seconds: 1 / VideoTarget.framesPerSecond - 0.004, preferredTimescale: 600))
            }
            if !input.append(sample) {
                fail(done, VideoTranscodeError.failed(writer.error?.localizedDescription ?? "the writer refused a frame"))
                return
            }
        }
    }

    private func complete(_ done: CheckedContinuation<Void, any Error>) {
        guard !finished else { return }
        if reader.status == .failed {
            fail(done, VideoTranscodeError.cannotRead(reader.error?.localizedDescription ?? "unknown"))
            return
        }
        finished = true
        writer.finishWriting {
            if self.writer.status == .completed {
                done.resume()
            } else {
                try? FileManager.default.removeItem(at: self.destination)
                done.resume(throwing: VideoTranscodeError.failed(self.writer.error?.localizedDescription ?? "the file was not finished"))
            }
        }
    }

    private func fail(_ done: CheckedContinuation<Void, any Error>, _ error: VideoTranscodeError) {
        guard !finished else { return }
        finished = true
        reader.cancelReading()
        writer.cancelWriting()
        try? FileManager.default.removeItem(at: destination)
        done.resume(throwing: error)
    }
}

/// Which of an export's inputs have no more samples; touched on the export's queue alone.
private final class PendingInputs: @unchecked Sendable {
    private var open: Int
    private var closedInputs: Set<ObjectIdentifier> = []

    init(count: Int) { open = count }

    var allClosed: Bool { open == 0 }
    func closed(_ input: AVAssetWriterInput) -> Bool { closedInputs.contains(ObjectIdentifier(input)) }
    func close(_ input: AVAssetWriterInput) {
        if closedInputs.insert(ObjectIdentifier(input)).inserted { open -= 1 }
    }
}
