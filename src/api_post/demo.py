"""Explicitly started loopback-only fixture. Never calls a model/vendor."""

import argparse
import json
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 8 * 1024 * 1024:
            self.send_error(413)
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode(errors="replace")
        try:
            if self.path.startswith("/slow"):
                time.sleep(3)
            if self.path.startswith("/sse"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for text in ["你好", "，", "流式响应", "已连通。"]:
                    packet = (
                        "data: "
                        + json.dumps(
                            {
                                "choices": [
                                    {"delta": {"content": text}, "finish_reason": None}
                                ]
                            },
                            ensure_ascii=False,
                        )
                        + "\r\n\r\n"
                    ).encode()
                    # Deliberately split UTF-8 and CRLF across writes.
                    for i in range(0, len(packet), 7):
                        self.wfile.write(packet[i : i + 7])
                        self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            value = {
                "ok": not self.path.startswith("/error"),
                "method": self.command,
                "path": self.path,
                "body": body,
                "headers": dict(self.headers),
                "message": "本机模拟接口，未调用真实模型",
            }
            payload = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(400 if self.path.startswith("/error") else 200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("X-Demo-Server", "api-post-local")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
