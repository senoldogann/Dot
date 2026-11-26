import SwiftUI
import UIKit

/// A picture shown whole (item 1904): fitted inside the space it is given, never cropped, on a dark backdrop; a pinch
/// zooms up to four times and the picture pans; a double tap zooms to two and a half times at the tapped point, or back
/// to fitted. UIKit's own scroll view does the zoom, the pan and the bounce.
struct ZoomableImage: UIViewRepresentable {
    let image: UIImage
    let label: String

    func makeUIView(context: Context) -> ZoomingScrollView {
        ZoomingScrollView(image: image, label: label)
    }

    func updateUIView(_ view: ZoomingScrollView, context: Context) {}
}

/// The scroll view of `ZoomableImage`: the picture's fitted scale is its smallest, worked out again when its size
/// changes (a sheet's detent, a rotation), and the picture stays centred while it is smaller than the view.
final class ZoomingScrollView: UIScrollView, UIScrollViewDelegate {
    private let imageView: UIImageView
    private var fittedFor: CGSize = .zero

    init(image: UIImage, label: String) {
        imageView = UIImageView(image: image)
        super.init(frame: .zero)
        delegate = self
        backgroundColor = .black
        showsHorizontalScrollIndicator = false
        showsVerticalScrollIndicator = false
        contentInsetAdjustmentBehavior = .never
        decelerationRate = .fast
        imageView.frame = CGRect(origin: .zero, size: image.size)
        imageView.isAccessibilityElement = true
        imageView.accessibilityLabel = label
        imageView.accessibilityTraits = .image
        imageView.accessibilityIdentifier = "screenshot.image"
        addSubview(imageView)
        let doubleTap: UITapGestureRecognizer = UITapGestureRecognizer(target: self, action: #selector(toggleZoom(_:)))
        doubleTap.numberOfTapsRequired = 2
        addGestureRecognizer(doubleTap)
    }

    required init?(coder: NSCoder) { nil }

    override func layoutSubviews() {
        super.layoutSubviews()
        let size: CGSize = imageView.image?.size ?? .zero
        if bounds.size != fittedFor, bounds.width > 0, bounds.height > 0, size.width > 0, size.height > 0 {
            fittedFor = bounds.size
            let fitted: CGFloat = min(bounds.width / size.width, bounds.height / size.height)
            minimumZoomScale = fitted
            maximumZoomScale = fitted * 4
            zoomScale = fitted
        }
        centre()
    }

    func viewForZooming(in scrollView: UIScrollView) -> UIView? { imageView }

    func scrollViewDidZoom(_ scrollView: UIScrollView) { centre() }

    /// Insets that keep a picture smaller than the view in its middle.
    private func centre() {
        let x: CGFloat = max((bounds.width - contentSize.width) / 2, 0)
        let y: CGFloat = max((bounds.height - contentSize.height) / 2, 0)
        contentInset = UIEdgeInsets(top: y, left: x, bottom: y, right: x)
    }

    @objc private func toggleZoom(_ tap: UITapGestureRecognizer) {
        if zoomScale > minimumZoomScale * 1.01 {
            setZoomScale(minimumZoomScale, animated: true)
            return
        }
        let point: CGPoint = tap.location(in: imageView)
        let scale: CGFloat = min(minimumZoomScale * 2.5, maximumZoomScale)
        let width: CGFloat = bounds.width / scale
        let height: CGFloat = bounds.height / scale
        zoom(to: CGRect(x: point.x - width / 2, y: point.y - height / 2, width: width, height: height), animated: true)
    }
}
