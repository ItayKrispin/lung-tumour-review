"""Local review server. Run: .venv/bin/python -m app.server"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.geometry import lung_png
from app.index_cases import load_catalog
from app.session import ReviewSession

HOST = "127.0.0.1"
PORT = 8000
STATIC = Path(__file__).resolve().parent / "static"
LOCK = threading.Lock()


class ReviewHandler(BaseHTTPRequestHandler):
    index: dict
    session: ReviewSession

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            with LOCK:
                if path == "/":
                    self._file("text/html; charset=utf-8", (STATIC / "index.html").read_bytes())
                elif path.startswith("/static/"):
                    self._static(path)
                elif path == "/api/cases":
                    self._json(200, self.session.public_cases())
                elif path == "/api/export.csv":
                    body = self.session.csv_all().encode()
                    self._file("text/csv; charset=utf-8", body, "reviewed-visits.csv")
                elif path.startswith("/api/cases/") and path.endswith(".png"):
                    self._slice(path)
                elif path.startswith("/api/cases/") and path.endswith("/export.csv"):
                    case_id = path.split("/")[3]
                    body = self.session.csv_text(case_id).encode()
                    self._file("text/csv; charset=utf-8", body, f"{case_id}.csv")
                else:
                    self._json(404, {"error": "Not found."})
        except KeyError as exc:
            self._json(404, {"error": str(exc) or "Not found."})
        except Exception as exc:  # noqa: BLE001 - the page shows the message
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path).rstrip("/")
        parts = [part for part in path.split("/") if part]
        try:
            payload = self._body()
            if path == "/api/export.csv":
                self._export_selected(payload)
                return
            with LOCK:
                if path == "/api/inbox/rescan":
                    self._json(200, self.session.rescan_inbox())
                elif len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "open":
                    self._json(200, self.session.open_case(parts[2]))
                elif len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "timepoint":
                    self._json(200, self.session.set_timepoint(parts[2], str(payload.get("timepoint", ""))))
                elif len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "reset":
                    self._json(200, self.session.reset_visit(parts[2]))
                elif len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "nodules":
                    self._json(
                        200,
                        self.session.propose(parts[2], int(payload["slice"]), float(payload["x"]), float(payload["y"])),
                    )
                elif len(parts) == 6 and parts[3] == "nodules" and parts[5] == "paint":
                    self._json(
                        200,
                        self.session.paint_points(
                            parts[2],
                            int(parts[4]),
                            int(payload["slice"]),
                            payload.get("points", []),
                            float(payload.get("radius", 10)),
                            str(payload.get("mode", "paint")),
                        ),
                    )
                elif len(parts) == 6 and parts[3] == "nodules" and parts[5] == "status":
                    self._json(200, self.session.set_status(parts[2], int(parts[4]), str(payload["status"])))
                elif len(parts) == 6 and parts[3] == "nodules" and parts[5] == "axes":
                    self._json(
                        200,
                        self.session.set_axes(
                            parts[2],
                            int(parts[4]),
                            int(payload["slice"]),
                            payload["long"],
                            payload["short"],
                        ),
                    )
                elif len(parts) == 6 and parts[3] == "nodules" and parts[5] == "fields":
                    self._json(200, self.session.update_fields(parts[2], int(parts[4]), payload))
                else:
                    self._json(404, {"error": "Not found."})
        except KeyError as exc:
            self._json(404, {"error": str(exc) or "Not found."})
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        parts = [part for part in unquote(urlparse(self.path).path).split("/") if part]
        try:
            with LOCK:
                if len(parts) == 5 and parts[:2] == ["api", "cases"] and parts[3] == "nodules":
                    self._json(200, self.session.delete(parts[2], int(parts[4])))
                else:
                    self._json(404, {"error": "Not found."})
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": str(exc)})

    def _export_selected(self, payload: dict) -> None:
        case_ids = payload.get("case_ids") if isinstance(payload, dict) else None
        if not isinstance(case_ids, list) or not any(str(case_id).strip() for case_id in case_ids):
            self._json(400, {"error": "Check at least one visit."})
            return
        with LOCK:
            body = self.session.csv_selected(case_ids).encode()
        self._file("text/csv; charset=utf-8", body, "reviewed-visits.csv")

    def _slice(self, path: str) -> None:
        pieces = path.split("/")
        case_id = pieces[3]
        index = int(pieces[5].split(".")[0])
        png = lung_png(self.session.slice_hu(case_id, index))
        self._file("image/png", png)

    def _static(self, path: str) -> None:
        name = path.removeprefix("/static/")
        file_path = (STATIC / name).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
            self._json(404, {"error": "Not found."})
            return
        kind = "text/plain; charset=utf-8"
        if name.endswith(".css"):
            kind = "text/css; charset=utf-8"
        elif name.endswith(".js"):
            kind = "text/javascript; charset=utf-8"
        self._file(kind, file_path.read_bytes())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, content_type: str, body: bytes, download_name: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        status = str(args[1]) if len(args) > 1 else ""
        if status.startswith(("4", "5")):
            super().log_message(fmt, *args)


def main() -> None:
    index = load_catalog()
    ReviewHandler.index = index
    ReviewHandler.session = ReviewSession(index)
    server = ThreadingHTTPServer((HOST, PORT), ReviewHandler)
    print(f"Lung tumour review at http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
