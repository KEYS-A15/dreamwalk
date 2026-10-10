"""Read-only, loopback API for one static graph and optional observed run."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .model import Graph


def create_server(graph: Graph, port: int = 8765, observed_run: dict | None = None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Reject cross-origin browser requests and DNS rebinding hostnames.
            host = self.headers.get("Host", "").split(":")[0]
            origin = self.headers.get("Origin")
            if host not in {"127.0.0.1", "localhost"} or (origin and origin != f"http://{self.headers.get('Host')}"):
                self.respond(403, {"error": "Only same-origin loopback requests are supported"})
                return
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    result = {"status": "ok", "schema_version": graph.schema_version,
                              "run_loaded": observed_run is not None}
                elif parsed.path == "/graph":
                    result = graph.to_dict()
                elif parsed.path == "/run" and observed_run is not None:
                    result = observed_run
                elif parsed.path == "/explain":
                    result = graph.explain(query["id"][0])
                elif parsed.path == "/focus":
                    result = graph.focus(query["id"][0], int(query.get("depth", ["1"])[0]))
                else:
                    self.respond(404, {"error": "Unknown endpoint"})
                    return
                self.respond(200, result)
            except KeyError:
                self.respond(404, {"error": "Missing or unknown node id"})
            except ValueError as error:
                self.respond(400, {"error": str(error)})

        def respond(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
