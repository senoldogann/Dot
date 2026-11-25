import SwiftUI

/// The phone's own preferences (Settings › General, Appearance, Chat): kept in `UserDefaults` under these keys, read
/// by the screens with `@AppStorage`. The values are the raw values of the enums below.
enum PrefKey {
    static let colorScheme: String = "colorScheme"
    static let textSize: String = "textSize"
    static let toolCallMode: String = "toolCallMode"
    static let responseStreaming: String = "responseStreaming"
    static let followUp: String = "followUp"
    static let readingWidth: String = "readingWidth"
    static let timestampFormat: String = "timestampFormat"
    static let confirmDelete: String = "confirmDelete"
    static let haptics: String = "haptics"
    static let dictationLanguage: String = "dictationLanguage"
}

/// Light, dark, or as the iPhone is.
enum ColorSchemeChoice: String, CaseIterable, Sendable {
    case system
    case light
    case dark

    var scheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }

    var title: String {
        switch self {
        case .system: String(localized: "System", comment: "Appearance: follow the iPhone's light or dark look")
        case .light: String(localized: "Light", comment: "Appearance: the light look")
        case .dark: String(localized: "Dark", comment: "Appearance: the dark look")
        }
    }
}

/// The text size inside the app: the iPhone's own (Dynamic Type) or one of the sizes the slider names.
enum TextSizeChoice: String, CaseIterable, Sendable {
    case system
    case xSmall
    case small
    case medium
    case large
    case xLarge
    case xxLarge
    case xxxLarge

    /// The slider's steps, smallest first (the iPhone's own setting is a switch above it).
    static let steps: [TextSizeChoice] = [.xSmall, .small, .medium, .large, .xLarge, .xxLarge, .xxxLarge]

    var dynamicTypeSize: DynamicTypeSize? {
        switch self {
        case .system: nil
        case .xSmall: .xSmall
        case .small: .small
        case .medium: .medium
        case .large: .large
        case .xLarge: .xLarge
        case .xxLarge: .xxLarge
        case .xxxLarge: .xxxLarge
        }
    }

    /// The body text's size at this step, in points (the system's body style at that size, plus Dot's one point).
    var bodyPoints: Int {
        switch self {
        case .system, .large: 17
        case .xSmall: 14
        case .small: 15
        case .medium: 16
        case .xLarge: 19
        case .xxLarge: 21
        case .xxxLarge: 23
        }
    }
}

/// How a chat's clock reads.
enum TimestampFormat: String, CaseIterable, Sendable {
    /// As the iPhone's region and its 24-hour switch say.
    case system
    case twentyFour
    case twelve

    var title: String {
        switch self {
        case .system: String(localized: "As the iPhone", comment: "General › Timestamps: the iPhone's own 12 or 24-hour clock")
        case .twentyFour: String(localized: "24-hour", comment: "General › Timestamps: 14:05")
        case .twelve: String(localized: "12-hour", comment: "General › Timestamps: 2:05 PM")
        }
    }

    static func current(_ defaults: UserDefaults) -> TimestampFormat {
        TimestampFormat(rawValue: defaults.string(forKey: PrefKey.timestampFormat) ?? "") ?? .system
    }
}

/// A time of day in the chosen format (pure; the locale gives the AM/PM words and the digits).
func clockWords(_ date: Date, format: TimestampFormat, locale: Locale) -> String {
    switch format {
    case .system:
        return date.formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(locale))
    case .twentyFour:
        return date.formatted(Date.VerbatimFormatStyle(format: "\(hour: .twoDigits(clock: .twentyFourHour, hourCycle: .zeroBased)):\(minute: .twoDigits)", timeZone: .current, calendar: .current).locale(locale))
    case .twelve:
        return date.formatted(Date.FormatStyle(date: .omitted, time: .omitted).hour(.defaultDigits(amPM: .abbreviated)).minute(.twoDigits).locale(locale))
    }
}

/// How wide a chat's prose runs on a wide screen (iPad), in characters of the body font per line (review S9: 60-75).
enum ReadingWidth: String, CaseIterable, Sendable {
    case narrow
    case standard
    case wide

    var charactersPerLine: Int {
        switch self {
        case .narrow: 60
        case .standard: 68
        case .wide: 75
        }
    }

    var title: String {
        switch self {
        case .narrow: String(localized: "Narrow", comment: "Appearance › Reading width: 60 characters a line")
        case .standard: String(localized: "Standard", comment: "Appearance › Reading width: 68 characters a line")
        case .wide: String(localized: "Wide", comment: "Appearance › Reading width: 75 characters a line")
        }
    }

    static func current(_ raw: String) -> ReadingWidth { ReadingWidth(rawValue: raw) ?? .standard }
}

/// The language dictation listens for.
enum DictationLanguage: String, CaseIterable, Sendable {
    /// The app's own language.
    case app
    case english
    case turkish

    /// The locale of the recognizer; the app's language for `.app`.
    func locale(app: Locale) -> Locale {
        switch self {
        case .app: app
        case .english: Locale(identifier: "en-US")
        case .turkish: Locale(identifier: "tr-TR")
        }
    }

    var title: String {
        switch self {
        case .app: String(localized: "The app's language", comment: "General › Voice › Dictation language: follow the app")
        case .english: String(localized: "English", comment: "A language's name")
        case .turkish: String(localized: "Turkish", comment: "A language's name")
        }
    }

    static func current(_ defaults: UserDefaults) -> DictationLanguage {
        DictationLanguage(rawValue: defaults.string(forKey: PrefKey.dictationLanguage) ?? "") ?? .app
    }
}

/// Applies the appearance preferences to everything beneath (the app's root).
struct AppearancePreferences: ViewModifier {
    @AppStorage(PrefKey.colorScheme) private var schemeRaw: String = ColorSchemeChoice.system.rawValue
    @AppStorage(PrefKey.textSize) private var sizeRaw: String = TextSizeChoice.system.rawValue

    func body(content: Content) -> some View {
        let scheme: ColorSchemeChoice = ColorSchemeChoice(rawValue: schemeRaw) ?? .system
        let size: TextSizeChoice = TextSizeChoice(rawValue: sizeRaw) ?? .system
        if let fixed = size.dynamicTypeSize {
            content.preferredColorScheme(scheme.scheme).dynamicTypeSize(fixed)
        } else {
            content.preferredColorScheme(scheme.scheme)
        }
    }
}

extension View {
    func appearancePreferences() -> some View { modifier(AppearancePreferences()) }
}
