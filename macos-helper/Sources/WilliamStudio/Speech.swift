import AVFoundation
import Foundation
import Speech

/// On-device speech-to-text (free, private). Push-to-talk; partial results stream into `transcript`.
@MainActor
final class SpeechIntake: NSObject, ObservableObject {
    @Published var transcript = ""
    @Published var listening = false
    @Published var authorized = false
    @Published var level: Float = 0
    @Published var error: String?
    @Published var onDevice = true

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private let engine = AVAudioEngine()

    func requestAuthorization() {
        SFSpeechRecognizer.requestAuthorization { status in
            Task { @MainActor in self.authorized = status == .authorized }
        }
        AVCaptureDevice.requestAccess(for: .audio) { _ in }
    }

    func toggle() { listening ? stop() : start() }

    func start() {
        error = nil
        guard let recognizer, recognizer.isAvailable else { error = "Speech recognizer unavailable"; return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition, onDevice { req.requiresOnDeviceRecognition = true }
        req.taskHint = .dictation
        request = req
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else { error = "No microphone input"; return }
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 2048, format: format) { [weak self] buffer, _ in
            req.append(buffer)
            guard let ch = buffer.floatChannelData?[0] else { return }
            let n = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<n { sum += ch[i] * ch[i] }
            let rms = sqrt(sum / Float(max(n, 1)))
            Task { @MainActor in self?.level = min(1, rms * 12) }
        }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            self.error = "Audio engine: \(error.localizedDescription)"
            return
        }
        listening = true
        task = recognizer.recognitionTask(with: req) { [weak self] result, err in
            Task { @MainActor in
                guard let self else { return }
                if let result { self.transcript = result.bestTranscription.formattedString }
                if err != nil || (result?.isFinal ?? false) { self.stop() }
            }
        }
    }

    func stop() {
        guard listening || engine.isRunning else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        request?.endAudio()
        task?.cancel()
        task = nil
        request = nil
        listening = false
        level = 0
    }
}
