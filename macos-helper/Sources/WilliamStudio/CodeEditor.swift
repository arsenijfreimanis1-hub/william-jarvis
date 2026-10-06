import AppKit
import SwiftUI

/// NSTextView-backed code editor with line numbers and regex highlighting.
struct CodeEditor: NSViewRepresentable {
    @Binding var text: String
    var language: String
    var fontSize: CGFloat = 12.5
    var onSave: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder

        let tv = EditorTextView()
        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.allowsUndo = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = false
        tv.isContinuousSpellCheckingEnabled = false
        tv.usesFindBar = true
        tv.isIncrementalSearchingEnabled = true
        tv.font = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular)
        tv.textContainerInset = NSSize(width: 6, height: 8)
        tv.isHorizontallyResizable = true
        tv.isVerticallyResizable = true
        tv.autoresizingMask = [.width]
        tv.textContainer?.widthTracksTextView = false
        tv.textContainer?.containerSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        tv.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        tv.backgroundColor = NSColor.textBackgroundColor
        tv.onSave = onSave
        tv.string = text

        let gutter = LineNumberRuler(textView: tv)
        scroll.verticalRulerView = gutter
        scroll.hasVerticalRuler = true
        scroll.rulersVisible = true
        scroll.documentView = tv
        context.coordinator.textView = tv
        context.coordinator.rehighlight()
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = context.coordinator.textView else { return }
        tv.onSave = onSave
        context.coordinator.parent = self
        if tv.string != text && !context.coordinator.isEditing {
            let sel = tv.selectedRanges
            tv.string = text
            context.coordinator.rehighlight()
            tv.selectedRanges = sel.filter { ($0.rangeValue.location + $0.rangeValue.length) <= (text as NSString).length }
        }
        if context.coordinator.language != language {
            context.coordinator.language = language
            context.coordinator.rehighlight()
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: CodeEditor
        weak var textView: EditorTextView?
        var isEditing = false
        var language: String
        private var pending: DispatchWorkItem?

        init(_ parent: CodeEditor) { self.parent = parent; language = parent.language }

        func textDidChange(_ notification: Notification) {
            guard let tv = textView else { return }
            isEditing = true
            parent.text = tv.string
            isEditing = false
            pending?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.rehighlight() }
            pending = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.12, execute: work)
        }

        func rehighlight() {
            guard let tv = textView, let storage = tv.textStorage else { return }
            let sel = tv.selectedRanges
            Highlighter.apply(to: storage, lang: language, font: tv.font ?? NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular))
            tv.selectedRanges = sel
            tv.enclosingScrollView?.verticalRulerView?.needsDisplay = true
        }
    }
}

final class EditorTextView: NSTextView {
    var onSave: (() -> Void)?

    override func keyDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers == "s" {
            onSave?(); return
        }
        if event.keyCode == 48, !event.modifierFlags.contains(.command) { // tab → 4 spaces
            insertText("    ", replacementRange: selectedRange()); return
        }
        if event.keyCode == 36 { // return keeps indentation
            let ns = string as NSString
            let lineRange = ns.lineRange(for: NSRange(location: selectedRange().location, length: 0))
            let line = ns.substring(with: lineRange)
            let indent = line.prefix { $0 == " " || $0 == "\t" }
            let extra = line.trimmingCharacters(in: .whitespacesAndNewlines).hasSuffix(":") || line.trimmingCharacters(in: .whitespacesAndNewlines).hasSuffix("{") ? "    " : ""
            insertText("\n" + indent + extra, replacementRange: selectedRange()); return
        }
        super.keyDown(with: event)
    }

    override func didChangeText() {
        super.didChangeText()
        enclosingScrollView?.verticalRulerView?.needsDisplay = true
    }
}

final class LineNumberRuler: NSRulerView {
    weak var textView: NSTextView?

    init(textView: NSTextView) {
        self.textView = textView
        super.init(scrollView: textView.enclosingScrollView, orientation: .verticalRuler)
        clientView = textView
        ruleThickness = 44
        NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification, object: textView.enclosingScrollView?.contentView, queue: .main) { [weak self] _ in self?.needsDisplay = true }
    }

    required init(coder: NSCoder) { fatalError() }

    override func drawHashMarksAndLabels(in rect: NSRect) {
        guard let tv = textView, let lm = tv.layoutManager, let tc = tv.textContainer else { return }
        NSColor.controlBackgroundColor.setFill()
        rect.fill()
        let visible = tv.visibleRect
        let glyphRange = lm.glyphRange(forBoundingRect: visible, in: tc)
        let charRange = lm.characterRange(forGlyphRange: glyphRange, actualGlyphRange: nil)
        let text = tv.string as NSString
        var lineNumber = 1
        var idx = 0
        while idx < charRange.location {
            idx = NSMaxRange(text.lineRange(for: NSRange(location: idx, length: 0)))
            lineNumber += 1
        }
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.monospacedDigitSystemFont(ofSize: 10, weight: .regular), .foregroundColor: NSColor.tertiaryLabelColor]
        var charIndex = charRange.location
        while charIndex < NSMaxRange(charRange) || (charIndex == text.length && text.length == charRange.location) {
            let lineRange = text.lineRange(for: NSRange(location: charIndex, length: 0))
            let gr = lm.glyphRange(forCharacterRange: NSRange(location: lineRange.location, length: 0), actualCharacterRange: nil)
            let lineRect = lm.lineFragmentRect(forGlyphAt: min(gr.location, max(lm.numberOfGlyphs - 1, 0)), effectiveRange: nil, withoutAdditionalLayout: true)
            let y = lineRect.minY - visible.minY + tv.textContainerInset.height
            let s = "\(lineNumber)" as NSString
            let size = s.size(withAttributes: attrs)
            s.draw(at: NSPoint(x: ruleThickness - size.width - 6, y: y + (lineRect.height - size.height) / 2), withAttributes: attrs)
            lineNumber += 1
            if lineRange.length == 0 { break }
            charIndex = NSMaxRange(lineRange)
            if charIndex >= text.length { break }
        }
    }
}
