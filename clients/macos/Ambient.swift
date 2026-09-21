import AppKit
import AVFoundation
import Speech

struct Settings: Decodable {
    let engineURL: String
    let tokenFile: String
    var locale: String?
    static let folder = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Ambient")
    static func load() throws -> Settings {
        let settings = try JSONDecoder().decode(Settings.self, from: Data(contentsOf: folder.appendingPathComponent("config.json")))
        guard let url = URL(string: settings.engineURL), url.scheme == "https", url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
            throw Failure.message("Set an HTTPS engineURL in config.json")
        }
        return settings
    }
}
final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
struct HTTPFailure: Error, LocalizedError {
    let status: Int
    var errorDescription: String? { "Engine request failed (HTTP \(status)); listening stopped" }
}
final class AudioSink: @unchecked Sendable {
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    func set(_ value: SFSpeechAudioBufferRecognitionRequest?) { lock.lock(); defer { lock.unlock() }; request = value }
    func append(_ buffer: AVAudioPCMBuffer) { lock.lock(); defer { lock.unlock() }; request?.append(buffer) }
    func end() { lock.lock(); defer { lock.unlock() }; request?.endAudio(); request = nil }
}
enum Failure: Error, LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}
struct Utterance {
    let id = UUID().uuidString
    let text: String
    let start: Double
    let end: Double
}
@MainActor final class Ambient: NSObject, NSApplicationDelegate {
    var status: NSStatusItem!
    var settings: Settings?
    var token = ""
    var running = false
    var generation = 0
    var taskID = UUID()
    var sessionID: String?
    var epoch = 0
    var used = 0
    var queue: [Utterance] = []
    var sending = false
    var controlling = false
    var note = "Paused — microphone off"
    var lastDecision = "No turns yet"
    let audio = AVAudioEngine()
    let network = URLSession(configuration: .ephemeral, delegate: NoRedirect(), delegateQueue: nil)
    var recognizer: SFSpeechRecognizer?
    nonisolated let sink = AudioSink()
    var hadText = false
    var recognition: SFSpeechRecognitionTask?
    var silence: Timer?
    var taskLimit: Timer?
    var finalLimit: Timer?
    var tapInstalled = false
    var sealing = false
    var started = Date()
    var utteranceStart = 0.0

    func applicationDidFinishLaunching(_ notification: Notification) {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        refresh()
    }
    func refresh() {
        status.button?.title = running ? "● Ambient" : "Ambient"
        let menu = NSMenu()
        let heading = NSMenuItem(title: note, action: nil, keyEquivalent: "")
        menu.addItem(heading)
        menu.addItem(NSMenuItem(title: lastDecision, action: nil, keyEquivalent: ""))
        menu.addItem(.separator())
        for (title, selector, enabled) in [
            ("Start listening", #selector(start), !running && !controlling),
            ("Pause listening", #selector(pause), running),
            ("Discard session", #selector(discard), !controlling && sessionID != nil),
            ("Open dashboard", #selector(openDashboard), true),
            ("Open configuration folder", #selector(openConfig), true),
            ("Quit Ambient", #selector(quit), true)] {
            let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            item.target = self; item.isEnabled = enabled; menu.addItem(item)
        }
        menu.autoenablesItems = false
        status.menu = menu
    }
    func api(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        guard let settings, let url = URL(string: settings.engineURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else { throw Failure.message("Missing configuration") }
        var req = URLRequest(url: url); req.httpMethod = "POST"; req.timeoutInterval = 35
        req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await network.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw HTTPFailure(status: (response as? HTTPURLResponse)?.statusCode ?? 0)
        }
        guard data.count < 1_000_000, let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw Failure.message("Invalid engine response") }
        return result
    }
    @objc func start() {
        guard !running, !controlling else { return }
        do {
            settings = try Settings.load()
            let path = NSString(string: settings!.tokenFile).expandingTildeInPath
            let attributes = try FileManager.default.attributesOfItem(atPath: path)
            guard let permissions = attributes[.posixPermissions] as? NSNumber, permissions.intValue & 0o077 == 0 else { throw Failure.message("Token file must be private: chmod 600") }
            token = try String(contentsOfFile: path, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
            guard token.count >= 24 else { throw Failure.message("Invalid token file") }
        } catch { note = error.localizedDescription; refresh(); return }
        generation += 1; let g = generation
        controlling = true; note = "Requesting microphone and speech permission…"; refresh()
        Task {
            let mic = await AVCaptureDevice.requestAccess(for: .audio)
            let speech = await withCheckedContinuation { continuation in SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) } }
            guard generation == g else { return }
            guard mic, speech == .authorized else { controlling = false; note = "Permission needed in System Settings → Privacy & Security"; refresh(); return }
            recognizer = SFSpeechRecognizer(locale: Locale(identifier: settings?.locale ?? Locale.current.identifier))
            guard let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition else {
                controlling = false; note = "On-device speech unavailable for this locale; microphone off"; refresh(); return
            }
            do {
                if let sid = sessionID {
                    do {
                        let state = try await api("/v1/sessions/\(sid)/controls", ["action": "resume"])
                        epoch = state["epoch"] as? Int ?? epoch
                    } catch let error as HTTPFailure where error.status == 404 {
                        sessionID = nil; try await createSession(g)
                    }
                } else { try await createSession(g) }
                guard generation == g else { return }
                controlling = false; running = true; started = Date()
                try beginAudio(); note = "Listening · on-device transcription"; refresh()
            } catch { if generation == g { controlling = false; fail(error.localizedDescription) } }
        }
    }
    func createSession(_ expectedGeneration: Int) async throws {
        let state = try await api("/v1/sessions", ["name": "Ambient Mac"])
        guard let id = state["id"] as? String, let e = state["epoch"] as? Int else { throw Failure.message("Invalid session response") }
        guard generation == expectedGeneration else {
            _ = try await api("/v1/sessions/\(id)/controls", ["action":"pause"])
            throw Failure.message("Session creation cancelled")
        }
        sessionID = id; epoch = e; used = 0
    }
    func beginAudio() throws {
        let input = audio.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { throw Failure.message("No microphone available") }
        beginRecognition()
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            // The audio callback feeds only the current local recognition request.
            self?.sink.append(buffer)
        }
        tapInstalled = true; audio.prepare(); try audio.start()
    }
    func timer(_ seconds: TimeInterval, _ callback: @escaping @Sendable (Timer) -> Void) -> Timer {
        let timer = Timer(timeInterval: seconds, repeats: false, block: callback)
        RunLoop.main.add(timer, forMode: .common)
        return timer
    }
    func beginRecognition() {
        guard running else { return }
        silence?.invalidate(); taskLimit?.invalidate(); finalLimit?.invalidate()
        sealing = false; hadText = false; taskID = UUID(); let id = taskID; let g = generation
        utteranceStart = Date().timeIntervalSince(started) * 1000
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true; req.requiresOnDeviceRecognition = true
        req.addsPunctuation = true; sink.set(req)
        recognition = recognizer?.recognitionTask(with: req) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self, self.running, self.generation == g, self.taskID == id else { return }
                if let result {
                    if !result.bestTranscription.formattedString.isEmpty { self.hadText = true }
                    if result.isFinal {
                        let text = result.bestTranscription.formattedString
                        self.finishRecognition(text)
                        return
                    }
                    if !self.sealing {
                        self.silence?.invalidate()
                        self.silence = self.timer(1.5) { [weak self] _ in DispatchQueue.main.async { self?.seal() } }
                    }
                }
                if error != nil {
                    if self.sealing && !self.hadText { self.beginRecognition() }
                    else { self.fail("On-device speech failed; start again to retry") }
                }
            }
        }
        taskLimit = timer(50) { [weak self] _ in DispatchQueue.main.async { self?.seal() } }
    }
    func seal() {
        guard running, !sealing else { return }
        sealing = true; silence?.invalidate(); taskLimit?.invalidate()
        sink.end()
        finalLimit = timer(8) { [weak self] _ in
            DispatchQueue.main.async {
            guard let self, self.running else { return }
            // Never promote an unfinished partial. Rotate an idle/stalled recognition task.
            self.recognition?.cancel(); self.beginRecognition()
            }
        }
    }
    func finishRecognition(_ text: String) {
        finalLimit?.invalidate(); silence?.invalidate(); taskLimit?.invalidate()
        sink.set(nil); recognition = nil; taskID = UUID()
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !clean.isEmpty {
            guard clean.count <= 4000 else { fail("Utterance too long; microphone off"); return }
            guard queue.count < 8 else { fail("Engine cannot keep up; microphone off"); return }
            queue.append(Utterance(text: text, start: utteranceStart, end: Date().timeIntervalSince(started) * 1000))
            drain()
        }
        beginRecognition()
    }
    func drain() {
        guard running, !sending, !queue.isEmpty else { return }
        sending = true; let g = generation
        Task {
            defer { sending = false; if running { drain() } }
            do {
                if used >= 256 {
                    if let sid = sessionID { _ = try await api("/v1/sessions/\(sid)/controls", ["action":"pause"]) }
                    guard running, generation == g else { return }
                    try await createSession(g)
                }
                guard running, generation == g, let sid = sessionID, !queue.isEmpty else { return }
                let turn = queue.removeFirst()
                let result = try await api("/v1/turns", ["text":turn.text,"request_id":turn.id,"session_id":sid,"epoch":epoch,"source":"ambient-macos-on-device","start_ms":turn.start,"end_ms":turn.end])
                guard running, generation == g else { return }
                used += 1
                let decision = result["decision"] as? [String:Any] ?? [:]
                if let reason = decision["reason"] as? String, ["provider_error", "judgment_budget_exhausted", "capture_storage_unavailable"].contains(reason) { fail("Engine unavailable (\(reason)); microphone off"); return }
                lastDecision = "Last turn: \(decision["label"] as? String ?? "unknown") · \(used) turns"
                refresh()
            } catch { if generation == g { fail(error.localizedDescription) } }
        }
    }
    func stopLocal() {
        running = false; generation += 1; queue.removeAll(); taskID = UUID()
        silence?.invalidate(); taskLimit?.invalidate(); finalLimit?.invalidate()
        audio.stop(); if tapInstalled { audio.inputNode.removeTap(onBus: 0); tapInstalled = false }
        sink.end(); recognition?.cancel(); recognition = nil
    }
    func control(_ action: String, message: String) {
        stopLocal(); note = message; controlling = true; refresh()
        Task {
            defer { controlling = false; refresh() }
            guard let sid = sessionID else { return }
            do {
                let result = try await api("/v1/sessions/\(sid)/controls", ["action": action])
                epoch = result["epoch"] as? Int ?? epoch + 1
                if action == "discard" { sessionID = nil; used = 0; lastDecision = "Session discarded" }
            } catch { note = "Microphone off; server cancellation unconfirmed. Open dashboard." }
        }
    }
    func fail(_ message: String) { control("pause", message: message) }
    @objc func pause() { control("pause", message: "Paused — microphone off") }
    @objc func discard() { control("discard", message: "Discarded — microphone off") }
    @objc func openDashboard() {
        if let settings = try? Settings.load(), let url = URL(string: settings.engineURL) { NSWorkspace.shared.open(url) }
        else { openConfig() }
    }
    @objc func openConfig() {
        try? FileManager.default.createDirectory(at: Settings.folder, withIntermediateDirectories: true)
        NSWorkspace.shared.open(Settings.folder)
    }
    @objc func quit() {
        control("pause", message: "Stopping…")
        Task {
            for _ in 0..<40 { if !controlling { break }; try? await Task.sleep(nanoseconds: 100_000_000) }
            NSApp.terminate(nil)
        }
    }
}
if CommandLine.arguments.contains("--speech-capability") {
    let speech = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    print("en-US on-device supported: \(speech?.supportsOnDeviceRecognition ?? false); available: \(speech?.isAvailable ?? false)")
} else if CommandLine.arguments.contains("--self-test") {
    let sample = Data("{\"engineURL\":\"https://example.com\",\"tokenFile\":\"/private/token\"}".utf8)
    let config = try JSONDecoder().decode(Settings.self, from: sample)
    precondition(config.engineURL == "https://example.com")
    precondition(Utterance(text: "exact\ntext", start: 0, end: 1).text == "exact\ntext")
    print("Ambient configuration and transcript self-test passed (no microphone accessed)")
} else {
    MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = Ambient()
    app.delegate = delegate; app.setActivationPolicy(.accessory); app.run()
    }
}
