"""
server/tcp_server.py
=====================
Mini Swiggy V1 — Single-threaded TCP server.

What this server does:
  1. Binds to a TCP port and waits for client connections.
  2. Accepts one client at a time (V1 is sequential — no concurrency).
  3. Reads newline-delimited JSON requests from the client.
  4. Validates and dispatches each request to request_handler.py.
  5. Sends a newline-delimited JSON response back.
  6. Continues until the client disconnects, then waits for the next client.

Why newline-delimited JSON?
  TCP is a *stream* protocol. There are no guaranteed boundaries between
  sends and receives. If a client sends 200 bytes, the server might receive
  them as one 200-byte chunk, two 100-byte chunks, or 200 one-byte chunks.
  We solve this by ending every message with '\\n'. The server buffers data
  until it sees '\\n', then processes one complete message at a time.

Limitations (V1):
  - Only ONE client at a time. A second client must wait until the first
    disconnects. Concurrency (threading/asyncio) is a V2 feature.
  - No authentication. Any connected client can call any action.
  - In-process only. Not a distributed microservice.

Usage:
    python -m server.tcp_server
    python -m server.tcp_server --host 127.0.0.1 --port 5000
"""

import argparse
import json
import socket
import sys
import pathlib

# Add project root to path so 'database' package is importable.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from server.request_handler import handle_request

# -------------------------------------------------------------------
# Configuration defaults
# -------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

# Maximum bytes we will buffer for a single incomplete message.
# If a client sends more than this without a newline we close the connection.
# 64 KiB is generous for our small JSON payloads.
MAX_MESSAGE_BYTES = 64 * 1024  # 64 KiB

# How long (seconds) to wait for data from a connected client before
# giving up. Prevents a silent client from blocking the server forever.
CLIENT_RECV_TIMEOUT = 30.0


# -------------------------------------------------------------------
# Framing helpers
# -------------------------------------------------------------------
def send_response(conn_sock: socket.socket, response: dict) -> None:
    """
    Serialise *response* to JSON, append '\\n', and send over *conn_sock*.

    Every response MUST end with '\\n' so the client knows where it ends.
    """
    line = json.dumps(response, ensure_ascii=False) + "\n"
    conn_sock.sendall(line.encode("utf-8"))


def _make_error_response(request_id: str, code: str, message: str) -> dict:
    return {"request_id": request_id, "ok": False,
            "error": {"code": code, "message": message}}


# -------------------------------------------------------------------
# Per-client request loop
# -------------------------------------------------------------------
def serve_client(conn_sock: socket.socket, addr: tuple) -> None:
    """
    Read and process all requests from one connected client.

    Protocol loop:
      1. Read bytes into a buffer until '\\n' is found.
      2. Everything before '\\n' is one complete JSON message.
      3. Parse the JSON, dispatch, send response.
      4. Repeat until client disconnects or times out.

    Args:
        conn_sock: The accepted client socket.
        addr:      (host, port) tuple for logging.
    """
    print(f"[server] Client connected: {addr[0]}:{addr[1]}")
    buffer = b""          # accumulates partial data between recv() calls

    try:
        while True:
            # --- Read a chunk from the network ---
            try:
                chunk = conn_sock.recv(4096)
            except socket.timeout:
                print(f"[server] Client {addr} timed out — closing connection.")
                break

            if not chunk:
                # recv() returning b"" means the client closed the connection.
                print(f"[server] Client {addr} disconnected.")
                break

            buffer += chunk

            # Guard against a client that never sends '\\n'.
            if len(buffer) > MAX_MESSAGE_BYTES:
                print(f"[server] Buffer overflow from {addr} — closing connection.")
                err = _make_error_response(
                    "unknown", "MESSAGE_TOO_LARGE",
                    f"Message exceeded {MAX_MESSAGE_BYTES} bytes without a newline."
                )
                send_response(conn_sock, err)
                break

            # --- Process all complete messages in the buffer ---
            # A client might send two requests back-to-back; both could
            # arrive in one recv() call. We loop until no '\\n' remains.
            while b"\n" in buffer:
                # Split at the FIRST newline only.
                line_bytes, buffer = buffer.split(b"\n", 1)
                line = line_bytes.strip()

                if not line:
                    # Empty line — ignore and continue reading.
                    continue

                # --- Parse JSON ---
                request_id = "unknown"
                try:
                    raw = json.loads(line.decode("utf-8"))
                    # Try to extract request_id early for error echoing.
                    if isinstance(raw, dict) and isinstance(raw.get("request_id"), str):
                        request_id = raw["request_id"]
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    response = _make_error_response(
                        request_id, "MALFORMED_JSON",
                        "Could not parse request as JSON."
                    )
                    send_response(conn_sock, response)
                    continue   # Don't crash — try next message.

                # --- Dispatch and respond ---
                response = handle_request(raw)
                send_response(conn_sock, response)

    except OSError as exc:
        # Covers connection reset, broken pipe, etc.
        print(f"[server] Socket error with {addr}: {exc}")
    finally:
        conn_sock.close()
        print(f"[server] Connection to {addr} closed.")


# -------------------------------------------------------------------
# Main server loop
# -------------------------------------------------------------------
def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """
    Start the TCP server and accept clients one at a time.

    Steps:
      socket()  — create a TCP socket object
      bind()    — reserve (host, port) on this machine
      listen()  — mark the socket as ready to accept connections
      accept()  — block until a client connects; returns a new socket
                  for that specific client
      (loop)    — handle the client, then call accept() again

    Args:
        host: IP address to bind to. '127.0.0.1' means local machine only.
        port: Port number. Ports < 1024 require admin rights on most OSes.
    """
    # AF_INET = IPv4, SOCK_STREAM = TCP (reliable, ordered byte stream)
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SO_REUSEADDR lets us restart the server immediately after stopping it.
    # Without this, the OS keeps the port reserved for ~60 seconds (TIME_WAIT).
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_sock.bind((host, port))
    except OSError as exc:
        # Happens when another process is already using the port.
        print(f"[server] ERROR: Cannot bind to {host}:{port} — {exc}")
        print("[server] Is another server already running on this port?")
        sys.exit(1)

    # backlog=5: up to 5 clients can queue waiting for accept() before
    # new connections are refused. V1 processes them one at a time.
    server_sock.listen(5)
    print(f"[server] Mini Swiggy V1 TCP server listening on {host}:{port}")
    print("[server] Press Ctrl+C to stop.")

    try:
        while True:
            # accept() blocks here until a new client connects.
            # It returns:
            #   conn_sock — a NEW socket for this specific client
            #   addr      — (client_ip, client_port)
            conn_sock, addr = server_sock.accept()

            # Set a read timeout on the client socket.
            conn_sock.settimeout(CLIENT_RECV_TIMEOUT)

            # Handle all requests from this client (sequential V1 model).
            serve_client(conn_sock, addr)

    except KeyboardInterrupt:
        print("\n[server] Shutting down (Ctrl+C received).")
    finally:
        server_sock.close()
        print("[server] Server socket closed.")


# -------------------------------------------------------------------
# CLI entry point
# -------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mini Swiggy V1 TCP Server"
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Host to bind to (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_server(host=args.host, port=args.port)
