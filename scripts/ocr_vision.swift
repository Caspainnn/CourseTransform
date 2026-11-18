import Foundation
import Vision
import AppKit

// macOS Vision 中文 OCR：读入图片路径，输出识别到的文字到 stdout
let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("usage: ocr <image_path> [image_path...]\n", stderr)
    exit(1)
}

for path in args.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        fputs("无法读取图片: \(path)\n", stderr)
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do {
        try handler.perform([request])
        print("===== \(path) =====")
        if let obs = request.results {
            // Vision 按观察顺序返回，按纵坐标排序以保持从上到下、从左到右的阅读顺序
            let sorted = obs.sorted { a, b in
                let ay = a.boundingBox.origin.y, by = b.boundingBox.origin.y
                if abs(ay - by) > 0.005 { return ay > by }
                return a.boundingBox.origin.x < b.boundingBox.origin.x
            }
            for o in sorted {
                if let top = o.topCandidates(1).first {
                    print(top.string)
                }
            }
        }
        print("===== END =====")
    } catch {
        fputs("OCR 失败 \(path): \(error)\n", stderr)
    }
}
