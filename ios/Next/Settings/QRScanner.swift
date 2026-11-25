@preconcurrency import AVFoundation
import DotEngine
import SwiftUI
import UIKit
import os

/// Scans a QR code Settings › Phone on the Mac shows (PARITY "Pair by QR"): the code for Dot Next
/// (`GET /api/phone/pair-qr`, the address and the sign-in code, paired on one tap) or the address alone
/// (`GET /api/phone/qr/dot`, the code then typed). Camera denied or missing says so and leaves the typed way open.
struct QRScanSheet: View {
    let found: (PairingStart) -> Void
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var access: AVAuthorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)
    @State private var wrongCode: Bool = false

    var body: some View {
        NavigationStack {
            Group {
                if AVCaptureDevice.default(for: .video) == nil {
                    EmptyState(
                        title: String(localized: "No camera here", comment: "QR scan: the device has no camera (the simulator)"), systemImage: "camera",
                        detail: String(localized: "Type the Mac's address instead.", comment: "QR scan: the typed route"), actionTitle: nil, action: {})
                } else {
                    switch access {
                    case .authorized:
                        ZStack(alignment: .bottom) {
                            CameraScanner { payload in
                                if let start = pairingStart(payload) {
                                    found(start)
                                    dismiss()
                                } else {
                                    wrongCode = true
                                }
                            }
                            .ignoresSafeArea()
                            Text(
                                wrongCode
                                    ? String(localized: "That code is not your Mac's address. Scan the code under Settings › Phone on the Mac.", comment: "QR scan: a code that is not a Dot address")
                                    : String(localized: "Point the camera at the code in Dot › Settings › Phone on your Mac.", comment: "QR scan: what to point at"))
                                .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
                                .padding(Space.m)
                                .floatingGlass(in: Capsule())
                                .padding(Space.gutter)
                        }
                    case .notDetermined:
                        LoadingState(message: String(localized: "Asking for the camera…", comment: "QR scan: the camera permission is being asked"))
                            .task {
                                _ = await AVCaptureDevice.requestAccess(for: .video)
                                access = AVCaptureDevice.authorizationStatus(for: .video)
                            }
                    default:
                        EmptyState(
                            title: String(localized: "Dot Next may not use the camera", comment: "QR scan: camera denied"), systemImage: "camera",
                            detail: String(localized: "Allow it in iOS Settings, or type the Mac's address instead.", comment: "QR scan: what to do when denied"),
                            actionTitle: String(localized: "Open iOS Settings", comment: "Opens the app's page in iOS Settings")
                        ) {
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                }
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Scan the Mac's code", comment: "Title of the QR scan sheet"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes the sheet"), systemImage: "xmark") { dismiss() }
                }
            }
        }
    }
}

/// The camera with a QR reader (AVFoundation's metadata output): each code read is handed over once.
struct CameraScanner: UIViewControllerRepresentable {
    let read: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController { ScannerController(read: read) }
    func updateUIViewController(_ controller: ScannerController, context: Context) {}

    final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        private let session: AVCaptureSession = AVCaptureSession()
        private let read: (String) -> Void
        private var last: String?

        init(read: @escaping (String) -> Void) {
            self.read = read
            super.init(nibName: nil, bundle: nil)
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { preconditionFailure("not from a storyboard") }

        override func viewDidLoad() {
            super.viewDidLoad()
            guard let device = AVCaptureDevice.default(for: .video) else { return }
            let input: AVCaptureDeviceInput
            do {
                input = try AVCaptureDeviceInput(device: device)
            } catch {
                Logger(subsystem: "dev.dotagent.phone.next", category: "pairing").error("the camera could not be opened error=\(String(describing: error), privacy: .public)")
                return
            }
            guard session.canAddInput(input) else { return }
            session.addInput(input)
            let output: AVCaptureMetadataOutput = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else { return }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = [.qr]
            let preview: AVCaptureVideoPreviewLayer = AVCaptureVideoPreviewLayer(session: session)
            preview.videoGravity = .resizeAspectFill
            preview.frame = view.bounds
            view.layer.addSublayer(preview)
            let session: AVCaptureSession = session
            DispatchQueue.global(qos: .userInitiated).async { session.startRunning() }
        }

        override func viewDidLayoutSubviews() {
            super.viewDidLayoutSubviews()
            view.layer.sublayers?.first?.frame = view.bounds
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            let session: AVCaptureSession = session
            DispatchQueue.global(qos: .userInitiated).async { session.stopRunning() }
        }

        nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard let code = (metadataObjects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
            MainActor.assumeIsolated {
                guard code != last else { return }
                last = code
                read(code)
            }
        }
    }
}
