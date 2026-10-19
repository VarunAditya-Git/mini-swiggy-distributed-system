"""
gateway/http_gateway.py
=======================
Lightweight HTTP API Gateway for Mini Swiggy.

Architecture:
  Browser (HTML/CSS/JS)
    │  HTTP fetch()
    ▼
  ThreadingHTTPServer (gateway/http_gateway.py)
    │  TCP socket (NDJSON) — short-lived per request
    ▼
  TCP Server (server/tcp_server.py: 127.0.0.1:5000)
    │  handle_request()
    ▼
  SQLite Database (swiggy.db)

Non-negotiable Rules:
  - Never executes SQL directly; all database interaction goes through the TCP server.
  - Opens a fresh, short-lived TCP connection for each API request.
  - Does not implement business validation or pricing; the TCP server is authoritative.
  - Enforces customer ownership when exposing order receipts.
  - Protects static file serving against path traversal attacks.
"""

import argparse
import http.server
import json
import mimetypes
import os
import pathlib
import socket
import sys
import urllib.parse
import uuid

# Project paths
PROJECT_ROOT = pathlib.Path(__file__).parent.parent
WEB_DIR = PROJECT_ROOT / "web"

# Network defaults
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 5000

# Limits & Timeouts
MAX_BODY_BYTES = 64 * 1024  # 64 KiB
TCP_CONNECT_TIMEOUT = 3.0   # seconds
TCP_READ_TIMEOUT = 5.0      # seconds

# Backend error code to HTTP status code mapping
ERROR_STATUS_MAP = {
    "NOT_FOUND": 404,
    "INVALID_PAYLOAD": 422,
    "INVALID_REQUEST": 400,
    "ITEM_UNAVAILABLE": 409,
    "RESTAURANT_CLOSED": 409,
    "PAYMENT_ALREADY_COMPLETED": 409,
    "UNKNOWN_ACTION": 404,
    "MALFORMED_JSON": 400,
    "MESSAGE_TOO_LARGE": 413,
    "DB_ERROR": 500,
}


# -------------------------------------------------------------------
# TCP Forwarding Helper
# -------------------------------------------------------------------
def forward_tcp_request(
    action: str,
    payload: dict,
    tcp_host: str = DEFAULT_TCP_HOST,
    tcp_port: int = DEFAULT_TCP_PORT,
    timeout: float = TCP_READ_TIMEOUT,
) -> dict:
    """
    Connect to the TCP server, send exactly one NDJSON request line,
    read exactly one newline-delimited JSON response, and close the socket.

    Returns:
        The parsed response dictionary from the backend.

    Raises:
        ConnectionRefusedError: If the TCP server is not running.
        socket.timeout: If the TCP server does not respond within timeout.
        ConnectionError: If connection closes unexpectedly.
        ValueError: If response is malformed or oversized.
    """
    req_id = f"gw-{uuid.uuid4().hex[:8]}"
    request_data = {
        "request_id": req_id,
        "action": action,
        "payload": payload,
    }
    line = json.dumps(request_data, ensure_ascii=False) + "\n"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TCP_CONNECT_TIMEOUT)
    try:
        sock.connect((tcp_host, tcp_port))
        sock.settimeout(timeout)
        sock.sendall(line.encode("utf-8"))

        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("TCP backend closed connection before sending complete response.")
            buf += chunk
            if len(buf) > MAX_BODY_BYTES:
                raise ValueError(f"TCP backend response exceeded {MAX_BODY_BYTES} bytes.")

        line_bytes, _ = buf.split(b"\n", 1)
        return json.loads(line_bytes.decode("utf-8"))
    finally:
        try:
            sock.close()
        except OSError:
            pass


# -------------------------------------------------------------------
# Gateway Request Handler
# -------------------------------------------------------------------
class GatewayRequestHandler(http.server.BaseHTTPRequestHandler):
    """
    Handles HTTP requests:
      - /api/* routes forward to the backend TCP socket server.
      - All other routes serve static frontend assets from web/.
    """

    # Attributes configured on the server instance
    tcp_host = DEFAULT_TCP_HOST
    tcp_port = DEFAULT_TCP_PORT
    web_dir = WEB_DIR

    def log_message(self, format: str, *args) -> None:
        """Suppress noisy default logging; emit clean gateway logs."""
        print(f"[gateway] {self.address_string()} - {format % args}")

    def _send_json(self, status_code: int, data: dict) -> None:
        """Serialize data to JSON and send with proper headers."""
        response_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_error(self, status_code: int, code: str, message: str) -> None:
        """Send a standard JSON error response."""
        self._send_json(status_code, {
            "ok": False,
            "error": {"code": code, "message": message},
        })

    def _read_json_body(self) -> dict | None:
        """Read and parse the JSON request body with size limits."""
        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            self._send_error(400, "MISSING_BODY", "Request body is required.")
            return None

        try:
            length = int(content_length_header)
        except ValueError:
            self._send_error(400, "INVALID_CONTENT_LENGTH", "Content-Length header must be an integer.")
            return None

        if length <= 0:
            self._send_error(400, "EMPTY_BODY", "Request body cannot be empty.")
            return None

        if length > MAX_BODY_BYTES:
            self._send_error(413, "PAYLOAD_TOO_LARGE", f"Request body exceeds {MAX_BODY_BYTES} bytes limit.")
            return None

        body_bytes = self.rfile.read(length)
        try:
            data = json.loads(body_bytes.decode("utf-8"))
            if not isinstance(data, dict):
                self._send_error(400, "INVALID_JSON", "Request body must be a JSON object.")
                return None
            return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error(400, "MALFORMED_JSON", "Request body must be valid JSON.")
            return None

    def _forward_and_respond(self, action: str, payload: dict, success_status: int = 200) -> None:
        """Forward request to TCP server and translate the response to HTTP."""
        try:
            backend_res = forward_tcp_request(
                action=action,
                payload=payload,
                tcp_host=self.tcp_host,
                tcp_port=self.tcp_port,
            )
        except ConnectionRefusedError:
            self._send_error(
                503,
                "TCP_BACKEND_UNAVAILABLE",
                f"Could not connect to TCP backend at {self.tcp_host}:{self.tcp_port}. Is the TCP server running?",
            )
            return
        except socket.timeout:
            self._send_error(504, "TCP_BACKEND_TIMEOUT", "TCP backend timed out while processing request.")
            return
        except Exception as exc:
            self._send_error(502, "TCP_BACKEND_ERROR", f"Error communicating with TCP backend: {exc}")
            return

        # Determine HTTP status code based on backend result
        if backend_res.get("ok"):
            self._send_json(success_status, backend_res)
        else:
            err_code = backend_res.get("error", {}).get("code", "UNKNOWN_ERROR")
            http_status = ERROR_STATUS_MAP.get(err_code, 400)
            self._send_json(http_status, backend_res)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # -------------------------------------------------------------------
    # GET Dispatcher
    # -------------------------------------------------------------------
    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # 1. Health check endpoint
        if path == "/api/health":
            self._handle_health()
            return

        # 2. List restaurants
        if path == "/api/restaurants":
            self._forward_and_respond("list_restaurants", {})
            return

        # 3. List menu for restaurant: /api/restaurants/{id}/menu
        if path.startswith("/api/restaurants/") and path.endswith("/menu"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "restaurants" and parts[3] == "menu":
                try:
                    restaurant_id = int(parts[2])
                except ValueError:
                    self._send_error(400, "INVALID_PATH_PARAM", "restaurant_id in URL must be an integer.")
                    return
                self._forward_and_respond("list_menu", {"restaurant_id": restaurant_id})
                return

        # 4. Get order by ID with ownership verification: /api/orders/{order_id}?customer_id=1
        if path.startswith("/api/orders/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "orders":
                try:
                    order_id = int(parts[2])
                except ValueError:
                    self._send_error(400, "INVALID_PATH_PARAM", "order_id in URL must be an integer.")
                    return

                # Validate customer_id query parameter
                cust_param = query.get("customer_id")
                if not cust_param or not cust_param[0]:
                    self._send_error(
                        400,
                        "INVALID_REQUEST",
                        "Query parameter 'customer_id' is required to verify order ownership.",
                    )
                    return
                try:
                    customer_id = int(cust_param[0])
                except ValueError:
                    self._send_error(400, "INVALID_REQUEST", "Query parameter 'customer_id' must be an integer.")
                    return

                self._handle_get_order(order_id, customer_id)
                return

        # 5. Get order history for customer: /api/customers/{customer_id}/orders
        if path.startswith("/api/customers/") and path.endswith("/orders"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "customers" and parts[3] == "orders":
                try:
                    customer_id = int(parts[2])
                except ValueError:
                    self._send_error(400, "INVALID_PATH_PARAM", "customer_id in URL must be an integer.")
                    return
                self._forward_and_respond("get_order_history", {"customer_id": customer_id})
                return

        # 6. Any other unmatched /api route
        if path.startswith("/api/"):
            self._send_error(404, "NOT_FOUND", f"Unknown API endpoint '{path}'.")
            return

        # 7. Static file serving from web/
        self._serve_static_file(path)

    # -------------------------------------------------------------------
    # POST Dispatcher
    # -------------------------------------------------------------------
    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # 1. Create order: POST /api/orders
        if path == "/api/orders":
            body = self._read_json_body()
            if body is None:
                return
            self._forward_and_respond("create_order", body, success_status=201)
            return

        # 2. Simulate payment: POST /api/orders/{order_id}/payment
        if path.startswith("/api/orders/") and path.endswith("/payment"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "orders" and parts[3] == "payment":
                try:
                    order_id = int(parts[2])
                except ValueError:
                    self._send_error(400, "INVALID_PATH_PARAM", "order_id in URL must be an integer.")
                    return

                body = self._read_json_body()
                if body is None:
                    return

                # Ensure order_id from the URL path is passed in payload
                body["order_id"] = order_id
                self._forward_and_respond("simulate_payment", body)
                return

        # 3. Unmatched API routes
        if path.startswith("/api/"):
            self._send_error(404, "NOT_FOUND", f"Unknown API endpoint '{path}'.")
            return

        self._send_error(405, "METHOD_NOT_ALLOWED", "POST is only supported on /api/ endpoints.")

    # -------------------------------------------------------------------
    # Handlers & Helpers
    # -------------------------------------------------------------------
    def _handle_health(self) -> None:
        """Check gateway health and ping the backend TCP server."""
        try:
            res = forward_tcp_request("ping", {}, tcp_host=self.tcp_host, tcp_port=self.tcp_port, timeout=2.0)
            if res.get("ok"):
                self._send_json(200, {
                    "ok": True,
                    "gateway": "running",
                    "tcp_backend": "connected",
                    "tcp_target": f"{self.tcp_host}:{self.tcp_port}",
                })
                return
        except Exception as exc:
            self._send_json(503, {
                "ok": False,
                "gateway": "running",
                "tcp_backend": "disconnected",
                "error": str(exc),
                "tcp_target": f"{self.tcp_host}:{self.tcp_port}",
            })

    def _handle_get_order(self, order_id: int, customer_id: int) -> None:
        """Retrieve order from TCP backend and enforce customer ownership."""
        try:
            backend_res = forward_tcp_request(
                action="get_order",
                payload={"order_id": order_id},
                tcp_host=self.tcp_host,
                tcp_port=self.tcp_port,
            )
        except ConnectionRefusedError:
            self._send_error(503, "TCP_BACKEND_UNAVAILABLE", "TCP backend unreachable.")
            return
        except socket.timeout:
            self._send_error(504, "TCP_BACKEND_TIMEOUT", "TCP backend timed out.")
            return
        except Exception as exc:
            self._send_error(502, "TCP_BACKEND_ERROR", str(exc))
            return

        if not backend_res.get("ok"):
            err_code = backend_res.get("error", {}).get("code", "NOT_FOUND")
            http_status = ERROR_STATUS_MAP.get(err_code, 400)
            self._send_json(http_status, backend_res)
            return

        # Enforce ownership check using authoritative backend data
        order_data = backend_res.get("data", {})
        if order_data.get("customer_id") != customer_id:
            self._send_error(
                403,
                "FORBIDDEN",
                f"Access denied: order {order_id} does not belong to customer {customer_id}.",
            )
            return

        self._send_json(200, backend_res)

    def _serve_static_file(self, req_path: str) -> None:
        """
        Serve static frontend files with strict path-traversal protection.
        Maps '/' to 'index.html'.
        """
        clean_path = req_path.strip("/")
        if not clean_path:
            clean_path = "index.html"

        # Resolve path safely within web_dir
        base_dir = self.web_dir.resolve()
        requested_file = (base_dir / clean_path).resolve()

        # Path traversal guard
        try:
            requested_file.relative_to(base_dir)
        except ValueError:
            self._send_error(403, "FORBIDDEN", "Path traversal forbidden.")
            return

        if not requested_file.is_file():
            self._send_error(404, "NOT_FOUND", f"File '{clean_path}' not found.")
            return

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(str(requested_file))
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            content = requested_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type or "javascript" in mime_type or "json" in mime_type else mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except OSError as exc:
            self._send_error(500, "FILE_READ_ERROR", f"Could not read static file: {exc}")


# -------------------------------------------------------------------
# Server Factory and Runner
# -------------------------------------------------------------------
def create_gateway_server(
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
    tcp_host: str = DEFAULT_TCP_HOST,
    tcp_port: int = DEFAULT_TCP_PORT,
    web_dir: pathlib.Path = WEB_DIR,
) -> http.server.ThreadingHTTPServer:
    """Create and return a configured ThreadingHTTPServer instance."""
    handler_class = type(
        "ConfiguredGatewayHandler",
        (GatewayRequestHandler,),
        {
            "tcp_host": tcp_host,
            "tcp_port": tcp_port,
            "web_dir": web_dir,
        },
    )
    server = http.server.ThreadingHTTPServer((http_host, http_port), handler_class)
    return server


def run_gateway(
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
    tcp_host: str = DEFAULT_TCP_HOST,
    tcp_port: int = DEFAULT_TCP_PORT,
    web_dir: pathlib.Path = WEB_DIR,
) -> None:
    """Start the HTTP gateway server."""
    server = create_gateway_server(
        http_host=http_host,
        http_port=http_port,
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        web_dir=web_dir,
    )
    print("=" * 60)
    print(f"  Mini Swiggy HTTP Web Gateway")
    print("=" * 60)
    print(f"  HTTP Frontend URL : http://{http_host}:{http_port}")
    print(f"  TCP Backend Target: {tcp_host}:{tcp_port}")
    print(f"  Static Web Directory: {web_dir}")
    print("  Press Ctrl+C to stop the gateway.")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] Shutting down gateway (Ctrl+C received).")
    finally:
        server.server_close()
        print("[gateway] Gateway server closed.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mini Swiggy HTTP Web Gateway")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST, help=f"HTTP host (default: {DEFAULT_HTTP_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"HTTP port (default: {DEFAULT_HTTP_PORT})")
    parser.add_argument("--tcp-host", default=DEFAULT_TCP_HOST, help=f"TCP backend host (default: {DEFAULT_TCP_HOST})")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help=f"TCP backend port (default: {DEFAULT_TCP_PORT})")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_gateway(
        http_host=args.host,
        http_port=args.port,
        tcp_host=args.tcp_host,
        tcp_port=args.tcp_port,
    )
