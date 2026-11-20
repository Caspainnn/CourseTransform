"""Command-line and local review entry points for PPTNative V1.0."""

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import uuid

from .models import read, save
from .pipeline import confirm, create, resume, revise, simulate


def serve(job, port):
    requests = job / "review-requests"
    requests.mkdir(exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(job), **kwargs)

        def reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                current = read(job / "job.json")["current"]
                self.send_response(302)
                self.send_header("Location", f"/versions/{current}/review.html")
                self.end_headers()
                return
            if self.path.startswith("/requests/"):
                request_id = self.path.rsplit("/", 1)[-1]
                if len(request_id) != 32 or not all(c in "0123456789abcdef" for c in request_id):
                    self.reply(400, {"error": "Invalid request ID"})
                    return
                target = requests / f"{request_id}.json"
                if not target.exists():
                    self.reply(404, {"error": "Unknown request"})
                    return
                self.reply(200, read(target))
                return
            super().do_GET()

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{port}":
                self.reply(403, {"error": "Use the local review page"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 20000:
                    raise ValueError("Feedback too long")
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if data["base_version"] != read(job / "job.json")["current"]:
                    self.reply(409, {"error": "This version is stale. Refresh the current review page."})
                    return
                if self.path == "/confirm":
                    confirm(job, data["base_version"])
                    self.reply(200, {"status": "confirmed", "version": data["base_version"]})
                    return
                if self.path != "/feedback":
                    self.reply(404, {"error": "Unknown operation"})
                    return
                if not data["text"].strip():
                    raise ValueError("Enter feedback")
                request_id = uuid.uuid4().hex
                path = requests / f"{request_id}.json"
                save(path, {"status": "queued", "base_version": data["base_version"]})

                def process():
                    try:
                        save(path, {"status": "running", "base_version": data["base_version"]})
                        result = revise(job, data["text"], data["base_version"])
                        payload = {"status": "complete", **result}
                        if "version" in result:
                            payload["review_url"] = f"/versions/{result['version']}/review.html"
                        save(path, payload)
                    except Exception as error:
                        save(path, {"status": "failed", "error": str(error)})

                threading.Thread(target=process, daemon=True).start()
                self.reply(202, {"request_id": request_id, "status": "queued"})
            except (ValueError, KeyError, RuntimeError) as error:
                self.reply(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PPTNative review http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


def main():
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create")
    make.add_argument("--job", type=Path, required=True)
    make.add_argument("--template", type=Path, required=True)
    make.add_argument("--outline", type=Path, required=True)
    edit = commands.add_parser("feedback")
    edit.add_argument("--job", type=Path, required=True)
    edit.add_argument("--base-version", required=True)
    edit.add_argument("--text", required=True)
    demo = commands.add_parser("simulate")
    demo.add_argument("--job", type=Path, required=True)
    demo.add_argument("--kind", choices=["merge_pages", "set_title"], required=True)
    web = commands.add_parser("serve")
    web.add_argument("--job", type=Path, required=True)
    web.add_argument("--port", type=int, default=8768)
    restart = commands.add_parser("resume")
    restart.add_argument("--job", type=Path, required=True)
    check = commands.add_parser("recheck")
    check.add_argument("--job", type=Path, required=True)
    check.add_argument("--version", required=True)
    args = parser.parse_args()
    job = args.job.resolve()
    if args.command == "create":
        result = create(job, args.template.resolve(), args.outline.resolve())
    elif args.command == "resume":
        result = resume(job)
    elif args.command == "recheck":
        from .pipeline import recheck
        result = recheck(job, args.version)
    elif args.command == "feedback":
        result = revise(job, args.text, args.base_version)
    elif args.command == "simulate":
        text, version = simulate(job, args.kind)
        print(f"SIMULATED USER FEEDBACK: {text}", flush=True)
        result = revise(job, text, version, simulation=True)
    else:
        serve(job, args.port)
        return
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
