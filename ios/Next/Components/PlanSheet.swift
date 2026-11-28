import DotEngine
import SwiftUI

/// The run's plan in its sheet (the owner's report of 2026-11-28): a head with how far it came ("2 of 6 done"), where the
/// run stands (at work on step 3, done, failed or stopped at a step) and a progress bar; then the steps on a vertical
/// line that joins their marks: a done step quiet with ✓, the step at work in ink with a filled ring (no spinner), later
/// steps dimmed, a failed one red, a stopped one with its stop mark. `steps` are the plan as its run left it
/// (`planAtEnd`). The sheet takes the height its content needs (`FittedSheetChrome`).
struct PlanSheet: View {
    let steps: [PlanStep]
    let end: PlanEnd

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            head
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    PlanStepRow(step: step, isLast: index == steps.count - 1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("plan.sheet")
    }

    private var done: Int { steps.filter { $0.status == "done" }.count }

    @ViewBuilder private var head: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(String(localized: "\(done) of \(steps.count) done", comment: "Plan sheet head; the arguments are the steps done and all steps"))
                .textRole(.headline).foregroundStyle(Tone.text).tabularNumbers()
                .accessibilityIdentifier("plan.count")
            Text(stateWords).textRole(.footnote).foregroundStyle(end == .failed ? Tone.failure : Tone.secondary)
                .accessibilityIdentifier("plan.state")
            PlanBar(done: done, total: steps.count, end: end)
                .padding(.top, Space.xxs)
        }
    }

    private var stateWords: String {
        let at: Int? = planCurrentStep(steps)
        switch end {
        case .done: return String(localized: "The run is done", comment: "Plan sheet: the run ended done")
        case .failed:
            guard let at else { return String(localized: "The run failed", comment: "Plan sheet: the run failed") }
            return String(localized: "Failed at step \(at)", comment: "Plan sheet: the run failed at a step; the argument is its number")
        case .stopped:
            guard let at else { return String(localized: "Stopped", comment: "Plan sheet: the run was stopped") }
            return String(localized: "Stopped at step \(at)", comment: "Plan sheet: the run was stopped at a step; the argument is its number")
        case .working:
            guard let at else { return String(localized: "Finishing", comment: "Plan sheet: every step is done and the run is ending") }
            return String(localized: "Working on step \(at)", comment: "Plan sheet: the step the run works on; the argument is its number")
        }
    }
}

/// How far the plan came, as a thin bar: done steps in ink, red once the run failed.
private struct PlanBar: View {
    let done: Int
    let total: Int
    let end: PlanEnd

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Tone.nested)
                Capsule().fill(end == .failed ? Tone.failure : Tone.text)
                    .frame(width: total == 0 ? 0 : proxy.size.width * CGFloat(done) / CGFloat(total))
            }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
    }
}

/// One step on the line: its mark, the line down to the next step's mark, and its words.
private struct PlanStepRow: View {
    let step: PlanStep
    let isLast: Bool
    @ScaledMetric(relativeTo: .body) private var mark: CGFloat = 22

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            VStack(spacing: 0) {
                PlanMark(status: step.status).frame(width: mark, height: mark)
                if !isLast {
                    Rectangle().fill(step.status == "done" ? Tone.text.opacity(0.35) : Tone.separator)
                        .frame(width: 2).frame(maxHeight: .infinity)
                }
            }
            .frame(width: mark)
            Text(step.text)
                .textRole(.body)
                .fontWeight(isCurrent ? .semibold : .regular)
                .foregroundStyle(ink)
                .strikethrough(step.status == "skipped")
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 1)
                .padding(.bottom, isLast ? 0 : Space.m)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: "\(step.text), \(statusWords)"))
        .accessibilityIdentifier("plan.step")
    }

    private var isCurrent: Bool { ["in_progress", "failed", "stopped"].contains(step.status) }

    private var ink: Color {
        switch step.status {
        case "in_progress", "stopped": Tone.text
        case "failed": Tone.failure
        case "done", "skipped": Tone.tertiary
        default: Tone.secondary
        }
    }

    private var statusWords: String {
        switch step.status {
        case "done": String(localized: "done", comment: "VoiceOver: a plan step's state")
        case "in_progress": String(localized: "in progress", comment: "VoiceOver: a plan step's state")
        case "failed": String(localized: "failed", comment: "VoiceOver: a plan step's state")
        case "stopped": String(localized: "stopped", comment: "VoiceOver: a plan step where the run was stopped")
        case "skipped": String(localized: "skipped", comment: "VoiceOver: a plan step's state")
        default: String(localized: "to do", comment: "VoiceOver: a plan step's state")
        }
    }
}

/// A step's mark: ✓ filled for done, a filled ring for the step at work, a red ✕ for failed, a stop for stopped, a dash
/// for skipped, an empty ring for later.
private struct PlanMark: View {
    let status: String

    var body: some View {
        switch status {
        case "done":
            Image(systemName: "checkmark.circle.fill").resizable().scaledToFit().foregroundStyle(Tone.text.opacity(0.55))
        case "in_progress":
            ZStack {
                Circle().stroke(Tone.text, lineWidth: 2)
                Circle().fill(Tone.text).padding(5)
            }
        case "failed":
            Image(systemName: "xmark.circle.fill").resizable().scaledToFit().foregroundStyle(Tone.failure)
        case "stopped":
            Image(systemName: "stop.circle.fill").resizable().scaledToFit().foregroundStyle(Tone.text)
        case "skipped":
            Image(systemName: "minus.circle").resizable().scaledToFit().foregroundStyle(Tone.tertiary)
        default:
            Circle().stroke(Tone.tertiary, lineWidth: 1.5).padding(1)
        }
    }
}

/// An inspector sheet that takes the height its content needs, up to the large detent (the plan of three steps is no
/// tall empty sheet); otherwise `SheetChrome`.
struct FittedSheetChrome: ViewModifier {
    let title: String
    let close: () -> Void
    @State private var height: CGFloat = 320

    func body(content: Content) -> some View {
        NavigationStack {
            ScrollView {
                content
                    .padding(.horizontal, Space.gutter)
                    .padding(.top, Space.xs)
                    .padding(.bottom, Space.xl)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { height = $0 }
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollContentBackground(.hidden)
            .background(Tone.sheet)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                }
            }
        }
        // The content and the bar's height (about 60 pt), never under the medium detent's look of a small sheet.
        .presentationDetents([.height(max(height + 72, 240)), .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
    }
}

extension View {
    func fittedInspectorSheet(title: String, close: @escaping () -> Void) -> some View {
        modifier(FittedSheetChrome(title: title, close: close))
    }
}
