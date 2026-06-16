"""Simple Python web application for Docker demo."""
import os
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
START_TIME = time.time()


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler with health check endpoints."""

    def _send_response(self, status: int, body: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Container-Id", os.uname().nodename)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path == "/healthz":
            self._send_response(200, {"status": "ok", "uptime": round(time.time() - START_TIME, 2)})
        elif self.path == "/readyz":
            self._send_response(200, {"status": "ready"})
        elif self.path == "/":
            self._send_response(200, {
                "message": "Hello from Docker!",
                "hostname": os.uname().nodename,
                "version": os.getenv("APP_VERSION", "1.0.0"),
            })
        else:
            self._send_response(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        """Structured logging to stdout."""
        print(f'[ACCESS] {self.address_string()} - {fmt % args}')


if __name__ == "__main__":
    print(f"[BOOT] Starting server on {HOST}:{PORT}")
    server = HTTPServer((HOST, PORT), HealthHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[SHUTDOWN] Server stopped")
        server.server_close()
