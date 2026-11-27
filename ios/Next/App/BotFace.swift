import DotEngine
import SwiftUI
import UIKit

// A bot's face (PLAN §27, item 1881): the character the web draws for an agent (`character/skins.js`, items 101-102),
// still: its shape as the web's radial profile, its colour among the twelve, and two eyes cut out of the body. An agent
// without a look wears the shape and colour of its id's hue (`hues.js`), so it has the same face on the web and here.

/// The eight shapes of the web's customiser, as radial profiles of `samples` radii (angle 0 to the right, y down).
enum BotShape: String, CaseIterable, Sendable {
    case circle, pebble, squircle, capsule, triangle, hexagon, cloud, droplet

    static let samples: Int = 64

    /// The profile's radii, the largest near 1 (the web's numbers).
    var radii: [Double] {
        switch self {
        case .circle: return Array(repeating: 1, count: Self.samples)
        case .pebble: return normalized(angles.map { 1 + 0.075 * cos(2 * $0 + 0.5) + 0.035 * cos(3 * $0 + 2.1) }, peak: 1.02)
        case .squircle:
            return normalized(angles.map { angle in pow(pow(abs(cos(angle)), 4.2) + pow(abs(sin(angle)), 4.2), -1 / 4.2) }, peak: 1.15)
        case .capsule: return profile(of: hull(x1: -0.42, y1: 0, r1: 0.62, x2: 0.42, y2: 0, r2: 0.62))
        case .triangle: return polygonProfile(sides: 3, radius: 1.12, corner: 0.34, rotation: -90)
        case .hexagon: return polygonProfile(sides: 6, radius: 1.04, corner: 0.26, rotation: 0)
        case .cloud:
            let discs: [(Double, Double, Double)] = [(-0.44, 0.2, 0.54), (0.46, 0.2, 0.5), (0.02, 0.3, 0.6), (-0.24, -0.3, 0.48), (0.3, -0.24, 0.44)]
            return normalized(
                angles.map { angle in
                    discs.reduce(0) { best, disc in
                        let along: Double = cos(angle) * disc.0 + sin(angle) * disc.1
                        let reach: Double = along * along - (disc.0 * disc.0 + disc.1 * disc.1 - disc.2 * disc.2)
                        return reach < 0 ? best : max(best, along + reach.squareRoot())
                    }
                }, peak: 1.02)
        case .droplet: return normalized(profile(of: hull(x1: 0, y1: 0.28, r1: 0.66, x2: 0, y2: -0.96, r2: 0.05)), peak: 1.04)
        }
    }

    /// Where the eyes sit, as a share of the face's size below its centre: lower on the shapes whose top is a point.
    var eyeDrop: Double {
        switch self {
        case .triangle: 0.1
        case .droplet: 0.12
        case .cloud: 0.04
        default: -0.02
        }
    }

    private var angles: [Double] { (0..<Self.samples).map { Double($0) / Double(Self.samples) * 2 * .pi } }

    private func normalized(_ radii: [Double], peak: Double) -> [Double] {
        guard let top = radii.max(), top > 0 else { return radii }
        return radii.map { $0 * peak / top }
    }

    /// The convex hull of two discs (`hullOfCircles`).
    private func hull(x1: Double, y1: Double, r1: Double, x2: Double, y2: Double, r2: Double) -> [CGPoint] {
        let steps: Int = 96
        let dx: Double = x2 - x1
        let dy: Double = y2 - y1
        let distance: Double = max((dx * dx + dy * dy).squareRoot(), 1e-6)
        let base: Double = atan2(dy, dx)
        let spread: Double = acos(max(-1, min(1, (r1 - r2) / distance)))
        var points: [CGPoint] = []
        for index in 0...(steps / 2) {
            let angle: Double = base + spread + (2 * .pi - 2 * spread) * Double(index) / Double(steps / 2)
            points.append(CGPoint(x: x1 + cos(angle) * r1, y: y1 + sin(angle) * r1))
        }
        for index in 0...(steps / 2) {
            let angle: Double = base - spread + 2 * spread * Double(index) / Double(steps / 2)
            points.append(CGPoint(x: x2 + cos(angle) * r2, y: y2 + sin(angle) * r2))
        }
        return points
    }

    /// A regular polygon with round corners, as radii (`regularPolygonProfile`).
    private func polygonProfile(sides: Int, radius: Double, corner: Double, rotation: Double) -> [Double] {
        let turn: Double = rotation * .pi / 180
        let vertices: [CGPoint] = (0..<sides).map { index in
            let angle: Double = turn + Double(index) / Double(sides) * 2 * .pi
            return CGPoint(x: cos(angle) * (radius - corner), y: sin(angle) * (radius - corner))
        }
        var outline: [CGPoint] = []
        func normal(_ from: CGPoint, _ to: CGPoint) -> Double {
            let dx: Double = to.x - from.x
            let dy: Double = to.y - from.y
            let length: Double = max((dx * dx + dy * dy).squareRoot(), 1)
            return atan2(-dx / length, dy / length)
        }
        for index in 0..<sides {
            let previous: CGPoint = vertices[(index - 1 + sides) % sides]
            let current: CGPoint = vertices[index]
            let next: CGPoint = vertices[(index + 1) % sides]
            let start: Double = normal(previous, current)
            var delta: Double = normal(current, next) - start
            while delta > .pi { delta -= 2 * .pi }
            while delta < -.pi { delta += 2 * .pi }
            for step in 0...10 {
                let angle: Double = start + delta * Double(step) / 10
                outline.append(CGPoint(x: current.x + cos(angle) * corner, y: current.y + sin(angle) * corner))
            }
        }
        return profile(of: outline)
    }

    /// Any closed polygon as radii seen from the centre (`profileFromPolygon`).
    private func profile(of polygon: [CGPoint]) -> [Double] {
        angles.map { angle in
            let dx: Double = cos(angle)
            let dy: Double = sin(angle)
            var best: Double = 0
            for index in polygon.indices {
                let a: CGPoint = polygon[index]
                let b: CGPoint = polygon[(index + 1) % polygon.count]
                let ex: Double = b.x - a.x
                let ey: Double = b.y - a.y
                let den: Double = dx * ey - dy * ex
                if abs(den) < 1e-9 { continue }
                let t: Double = (a.x * ey - a.y * ex) / den
                let u: Double = (a.x * dy - a.y * dx) / den
                if t > best, u >= 0, u <= 1 { best = t }
            }
            return best
        }
    }
}

/// A radial profile drawn as a smooth closed path in its rect (radius 1 fills half the rect's shorter side, less a
/// margin so the largest radius stays inside).
struct ProfileShape: Shape {
    let radii: [Double]

    func path(in rect: CGRect) -> Path {
        let scale: Double = min(rect.width, rect.height) / 2 / 1.15
        let centre: CGPoint = CGPoint(x: rect.midX, y: rect.midY)
        let points: [CGPoint] = radii.enumerated().map { index, radius in
            let angle: Double = Double(index) / Double(radii.count) * 2 * .pi
            return CGPoint(x: centre.x + cos(angle) * radius * scale, y: centre.y + sin(angle) * radius * scale)
        }
        var path: Path = Path()
        guard let first = points.first else { return path }
        path.move(to: first)
        // Catmull-Rom through the samples, as the web's `closedPath`.
        let tension: Double = 1.0 / 6
        for index in points.indices {
            let p0: CGPoint = points[(index - 1 + points.count) % points.count]
            let p1: CGPoint = points[index]
            let p2: CGPoint = points[(index + 1) % points.count]
            let p3: CGPoint = points[(index + 2) % points.count]
            path.addCurve(
                to: p2, control1: CGPoint(x: p1.x + (p2.x - p0.x) * tension, y: p1.y + (p2.y - p0.y) * tension),
                control2: CGPoint(x: p2.x - (p3.x - p1.x) * tension, y: p2.y - (p3.y - p1.y) * tension))
        }
        path.closeSubpath()
        return path
    }
}

/// The twelve body colours (`--character-*` of `themes.css`); ink, brown and cream follow the appearance so a body is
/// never the colour of the ground.
enum BotColour: String, CaseIterable, Sendable {
    case ink, brown, red, orange, amber, green, teal, blue, violet, pink, grey, cream

    var color: Color {
        switch self {
        case .ink: Self.dynamic(light: 0x0a0a0c, dark: 0xecebe6)
        case .brown: Self.dynamic(light: 0x8b5e3c, dark: 0xa8744c)
        case .red: Self.fixed(0xe8483f)
        case .orange: Self.fixed(0xf08a24)
        case .amber: Self.fixed(0xf0b429)
        case .green: Self.fixed(0x3ecf8e)
        case .teal: Self.fixed(0x2fbfa0)
        case .blue: Self.fixed(0x3b93f0)
        case .violet: Self.fixed(0x8b5cf6)
        case .pink: Self.fixed(0xe152b0)
        case .grey: Self.fixed(0xa3a3a3)
        case .cream: Self.dynamic(light: 0xd9d4c6, dark: 0xf1efe9)
        }
    }

    private static func rgb(_ hex: UInt32) -> UIColor {
        UIColor(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255, alpha: 1)
    }

    private static func fixed(_ hex: UInt32) -> Color { Color(uiColor: rgb(hex)) }

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in rgb(traits.userInterfaceStyle == .dark ? dark : light) })
    }
}

/// The look a bot wears: its own, else its id's hue (`hues.js`: FNV-1a over the id's UTF-16 units, eight hues), Dot's
/// the ink circle of this app's mark.
func botLook(id: String, look: AgentLook?) -> (BotShape, BotColour) {
    if let look { return (BotShape(rawValue: look.shape) ?? .circle, BotColour(rawValue: look.colour) ?? .ink) }
    if id == dotAgentId { return (.circle, .ink) }
    let hue: Int = Int(hueHash(id) % 8)
    let colours: [BotColour] = [.red, .orange, .green, .teal, .blue, .violet, .pink, .brown]
    return (BotShape.allCases[hue], colours[hue])
}

/// FNV-1a over the UTF-16 code units of `id` (`hues.js` `hashId`).
func hueHash(_ id: String) -> UInt32 {
    var hash: UInt32 = 0x811c_9dc5
    for unit in id.utf16 {
        hash ^= UInt32(unit)
        hash = hash &* 0x0100_0193
    }
    return hash
}

/// A bot's face at a size: the body in its colour, two eyes cut out of it.
struct BotFace: View {
    let id: String
    let look: AgentLook?
    let size: CGFloat

    var body: some View {
        BotFaceDrawing(id: id, look: look, size: size, pose: .rest)
    }
}

/// A bot's face in a pose: the eyes where the pose looks, as open as it says (the whole face's bob and turn are its
/// wearer's, `LivingFace`).
struct BotFaceDrawing: View {
    let id: String
    let look: AgentLook?
    let size: CGFloat
    let pose: FacePose

    var body: some View {
        let (shape, colour): (BotShape, BotColour) = botLook(id: id, look: look)
        ProfileShape(radii: shape.radii)
            .fill(colour.color)
            .mask {
                ZStack {
                    Rectangle()
                    HStack(spacing: size * 0.15) {
                        Capsule().frame(width: size * 0.11, height: size * 0.22 * pose.open)
                        Capsule().frame(width: size * 0.11, height: size * 0.22 * pose.open)
                    }
                    .offset(x: size * pose.glance, y: size * shape.eyeDrop)
                    .blendMode(.destinationOut)
                }
                .compositingGroup()
            }
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

/// A bot's face that lives while a chat of it works (the owner's ask of 2026-11-27, after Grok's list): a short bob,
/// a glance to one side every few seconds, a blink, and now and then a flip; still at rest (done, waiting on the owner,
/// failed). Under Reduce Motion it does not move: it breathes in brightness. It draws only while on screen and while
/// the app is in front (`TimelineView` paused otherwise), thirty frames a second at most.
struct LivingBotFace: View {
    let id: String
    let look: AgentLook?
    let size: CGFloat
    let alive: Bool

    var body: some View {
        LivingFace(alive: alive, seed: hueHash(id), size: size) { pose in
            BotFaceDrawing(id: id, look: look, size: size, pose: pose)
        }
    }
}

/// The motion of a living face around any face drawn in a pose: the bob and the flip move the whole face, the pose
/// goes to the drawing for its eyes. `face.living` while it moves, for the UI tests.
struct LivingFace<Face: View>: View {
    let alive: Bool
    let seed: UInt32
    let size: CGFloat
    @ViewBuilder let face: (FacePose) -> Face
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.scenePhase) private var scenePhase: ScenePhase
    @State private var onScreen: Bool = false

    var body: some View {
        let moving: Bool = alive && onScreen && scenePhase == .active
        TimelineView(.animation(minimumInterval: reduceMotion ? 1.0 / 10 : 1.0 / 30, paused: !moving)) { timeline in
            let time: Double = timeline.date.timeIntervalSinceReferenceDate
            let pose: FacePose = alive && !reduceMotion ? livingPose(at: time, seed: seed) : .rest
            face(pose)
                .rotation3DEffect(.degrees(pose.turn), axis: (x: 0, y: 1, z: 0), perspective: 0.4)
                .offset(y: size * pose.bob)
                .opacity(alive && reduceMotion ? stillPulse(at: time) : 1)
        }
        .frame(width: size, height: size)
        .onAppear { onScreen = true }
        .onDisappear { onScreen = false }
        .accessibilityHidden(true)
        .accessibilityIdentifier(alive ? "face.living" : "face.rest")
    }
}
