import ImageIO
import UIKit

/// The sizes a picture is cut to on the phone, so that no camera-sized image is sent or held: what goes to the Mac (a
/// model reads at most about 1,600 px on the long side), what the composer shows of an attachment, and a screenshot as
/// the chat draws it.
enum ImageSizing {
    /// The long side of a photo sent with a message, in pixels.
    static let upload: CGFloat = 1600
    /// The long side of an attachment's preview in the composer, in pixels.
    static let preview: CGFloat = 240
    /// The long side of a screenshot drawn in the chat, in pixels.
    static let display: CGFloat = 1600
    /// The JPEG quality of a photo sent.
    static let quality: CGFloat = 0.8

    /// An image decoded from `data` with its long side at most `longest` pixels, upright; `nil` for data that is no
    /// image. ImageIO decodes straight to the smaller size, so the whole picture is never held in memory.
    static func decoded(_ data: Data, longest: CGFloat) -> UIImage? {
        guard let source: CGImageSource = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: NSNumber(value: false)] as CFDictionary) else { return nil }
        let options: [CFString: NSNumber] = [
            kCGImageSourceCreateThumbnailFromImageAlways: NSNumber(value: true),
            kCGImageSourceCreateThumbnailWithTransform: NSNumber(value: true),
            kCGImageSourceShouldCacheImmediately: NSNumber(value: true),
            kCGImageSourceThumbnailMaxPixelSize: NSNumber(value: Double(longest)),
        ]
        guard let image: CGImage = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        return UIImage(cgImage: image)
    }

    /// `image` drawn again with its long side at most `longest` pixels (a camera's or a scanner's picture), at scale 1;
    /// a smaller image is kept as it is.
    static func fitted(_ image: UIImage, longest: CGFloat) -> UIImage {
        let pixels: CGSize = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
        let target: CGSize = fittedSize(pixels, longest: longest)
        guard target != pixels else { return image }
        let format: UIGraphicsImageRendererFormat = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: target)) }
    }
}

/// `size` scaled down to fit `longest` on its long side, its proportions kept and rounded to whole pixels; a size that
/// fits already is given back as it is. Pure.
func fittedSize(_ size: CGSize, longest: CGFloat) -> CGSize {
    let long: CGFloat = max(size.width, size.height)
    guard long > longest, long > 0 else { return size }
    let factor: CGFloat = longest / long
    return CGSize(width: max(1, (size.width * factor).rounded()), height: max(1, (size.height * factor).rounded()))
}
