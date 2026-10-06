import AppKit
import Foundation

/// Small regex-based syntax highlighter for the IDE editor. Fast enough for files up to a few thousand lines.
enum Highlighter {
    struct Rule { let regex: NSRegularExpression; let color: NSColor }

    static let keywordColor = NSColor.systemPink
    static let stringColor = NSColor.systemRed
    static let commentColor = NSColor.systemGreen
    static let numberColor = NSColor.systemOrange
    static let typeColor = NSColor.systemTeal
    static let decoratorColor = NSColor.systemPurple

    static func language(for path: String) -> String {
        switch (path as NSString).pathExtension.lowercased() {
        case "py": return "python"
        case "swift": return "swift"
        case "js", "jsx", "ts", "tsx", "mjs": return "js"
        case "sh", "zsh", "bash": return "shell"
        case "md", "markdown": return "markdown"
        case "json": return "json"
        case "yml", "yaml": return "yaml"
        case "toml", "ini", "env", "cfg": return "ini"
        case "html", "xml", "plist": return "xml"
        case "css": return "css"
        default: return (path as NSString).lastPathComponent.hasPrefix(".env") ? "ini" : "plain"
        }
    }

    private static var cache: [String: [Rule]] = [:]

    static func rules(for lang: String) -> [Rule] {
        if let r = cache[lang] { return r }
        var out: [Rule] = []
        func add(_ pattern: String, _ color: NSColor, _ opts: NSRegularExpression.Options = []) {
            if let re = try? NSRegularExpression(pattern: pattern, options: opts) { out.append(Rule(regex: re, color: color)) }
        }
        let number = #"\b\d+(\.\d+)?\b"#
        switch lang {
        case "python":
            add(#"\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|yield|async|await|pass|break|continue|raise|in|not|and|or|is|None|True|False|global|nonlocal|del|assert)\b"#, keywordColor)
            add(#"\b[A-Z][A-Za-z0-9_]+\b"#, typeColor)
            add(number, numberColor)
            add(#"@[\w.]+"#, decoratorColor)
            add(#"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|f?\"(?:\\.|[^\"\\\n])*\"|f?'(?:\\.|[^'\\\n])*')"#, stringColor)
            add(#"#.*$"#, commentColor, .anchorsMatchLines)
        case "swift":
            add(#"\b(import|let|var|func|struct|class|enum|protocol|extension|return|if|else|guard|for|while|in|switch|case|default|break|continue|do|try|catch|throw|throws|async|await|actor|init|self|Self|super|true|false|nil|private|public|internal|fileprivate|static|final|override|some|any|where|defer|typealias|associatedtype|inout|mutating|lazy|weak|unowned|@MainActor|@Published|@State|@Binding|@EnvironmentObject|@StateObject|@ObservedObject|@AppStorage|@ViewBuilder|@escaping)\b"#, keywordColor)
            add(#"\b[A-Z][A-Za-z0-9_]+\b"#, typeColor)
            add(number, numberColor)
            add(#""(?:\\.|[^"\\\n])*""#, stringColor)
            add(#"//.*$"#, commentColor, .anchorsMatchLines)
            add(#"/\*[\s\S]*?\*/"#, commentColor)
        case "js":
            add(#"\b(const|let|var|function|return|if|else|for|while|do|switch|case|default|break|continue|new|class|extends|import|export|from|as|try|catch|finally|throw|async|await|typeof|instanceof|in|of|this|null|undefined|true|false|interface|type|enum|implements|readonly|public|private|protected|static)\b"#, keywordColor)
            add(#"\b[A-Z][A-Za-z0-9_]+\b"#, typeColor)
            add(number, numberColor)
            add(#"(`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')"#, stringColor)
            add(#"//.*$"#, commentColor, .anchorsMatchLines)
            add(#"/\*[\s\S]*?\*/"#, commentColor)
        case "shell":
            add(#"\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|in|return|exit|local|export|set|source|echo|cd|test)\b"#, keywordColor)
            add(#"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"#, typeColor)
            add(#"("(?:\\.|[^"\\])*"|'[^']*')"#, stringColor)
            add(#"#.*$"#, commentColor, .anchorsMatchLines)
        case "markdown":
            add(#"^#{1,6} .*$"#, keywordColor, .anchorsMatchLines)
            add(#"\*\*[^*]+\*\*"#, typeColor)
            add(#"`[^`\n]+`"#, stringColor)
            add(#"^```[\s\S]*?^```"#, commentColor, .anchorsMatchLines)
            add(#"\[[^\]]+\]\([^)]+\)"#, decoratorColor)
        case "json":
            add(#""(?:\\.|[^"\\])*"(?=\s*:)"#, typeColor)
            add(#":\s*"(?:\\.|[^"\\])*""#, stringColor)
            add(number, numberColor)
            add(#"\b(true|false|null)\b"#, keywordColor)
        case "yaml":
            add(#"^\s*[\w.-]+(?=\s*:)"#, typeColor, .anchorsMatchLines)
            add(#"("(?:\\.|[^"\\])*"|'[^']*')"#, stringColor)
            add(number, numberColor)
            add(#"\b(true|false|null|yes|no)\b"#, keywordColor)
            add(#"#.*$"#, commentColor, .anchorsMatchLines)
        case "ini":
            add(#"^\s*[\w.-]+(?=\s*=)"#, typeColor, .anchorsMatchLines)
            add(#"^\[.*\]$"#, keywordColor, .anchorsMatchLines)
            add(#"[#;].*$"#, commentColor, .anchorsMatchLines)
        case "xml":
            add(#"</?[\w:-]+|/?>"#, keywordColor)
            add(#"\b[\w:-]+(?==)"#, typeColor)
            add(#""[^"]*""#, stringColor)
            add(#"<!--[\s\S]*?-->"#, commentColor)
        case "css":
            add(#"[.#]?[\w-]+(?=\s*\{)"#, typeColor)
            add(#"[\w-]+(?=\s*:)"#, keywordColor)
            add(#"("[^"]*"|'[^']*')"#, stringColor)
            add(#"/\*[\s\S]*?\*/"#, commentColor)
        default:
            break
        }
        cache[lang] = out
        return out
    }

    static func apply(to storage: NSTextStorage, lang: String, font: NSFont) {
        let full = NSRange(location: 0, length: storage.length)
        storage.beginEditing()
        storage.setAttributes([.font: font, .foregroundColor: NSColor.textColor], range: full)
        let text = storage.string
        for rule in rules(for: lang) {
            rule.regex.enumerateMatches(in: text, options: [], range: full) { m, _, _ in
                if let r = m?.range { storage.addAttribute(.foregroundColor, value: rule.color, range: r) }
            }
        }
        storage.endEditing()
    }
}
