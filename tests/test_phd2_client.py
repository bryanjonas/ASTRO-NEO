"""Throwaway protocol-correctness test for Phd2Client -- no real PHD2,
verifies RPC/event interleaving and settle-detection logic against a fake
in-process server thread."""

import json
import socket
import sys
import threading
import time

sys.path.insert(0, "/app")

from app.services.phd2_client import Phd2Client, Phd2Error

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def _send(sock, obj):
    sock.sendall((json.dumps(obj) + "\r\n").encode())


def _recv_lines(sock, n, timeout=3.0):
    sock.settimeout(timeout)
    buf = b""
    lines = []
    deadline = time.time() + timeout
    while len(lines) < n and time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                lines.append(json.loads(line))
    return lines


# --- Fake PHD2 server: real TCP loopback, no mocking of socket internals ---
server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.bind(("127.0.0.1", 0))
server_sock.listen(1)
port = server_sock.getsockname()[1]

conn_holder = {}


def _accept():
    conn, _ = server_sock.accept()
    conn_holder["conn"] = conn
    # Initial burst, matching real PHD2 behavior confirmed live.
    _send(conn, {"Event": "Version", "PHDVersion": "2.6.14"})
    _send(conn, {"Event": "AppState", "State": "Stopped"})


accept_thread = threading.Thread(target=_accept, daemon=True)
accept_thread.start()

client = Phd2Client(host="127.0.0.1", port=port, timeout=5.0)
client.connect()
accept_thread.join(timeout=3.0)
conn = conn_holder["conn"]

check("initial AppState event populates app_state", client.app_state == "Stopped")

# --- Test 1: RPC request/response with an interleaved event ---
def _respond_get_app_state():
    reqs = _recv_lines(conn, 1)
    check("client sent get_app_state RPC", reqs and reqs[0].get("method") == "get_app_state")
    req_id = reqs[0]["id"]
    # Interleave an unrelated event before the actual response, matching
    # real PHD2 behavior (events arrive whenever, not just between calls).
    _send(conn, {"Event": "StarLost", "SNR": 0.5})
    _send(conn, {"jsonrpc": "2.0", "result": "Guiding", "id": req_id})


responder = threading.Thread(target=_respond_get_app_state, daemon=True)
responder.start()
result = client.get_app_state()
responder.join(timeout=3.0)
check("RPC result correctly extracted despite interleaved event", result == "Guiding")

# --- Test 2: guide_and_wait_for_settle success path ---
def _respond_guide_success():
    reqs = _recv_lines(conn, 1)
    check("client sent guide RPC", reqs and reqs[0].get("method") == "guide")
    req_id = reqs[0]["id"]
    _send(conn, {"jsonrpc": "2.0", "result": 0, "id": req_id})
    time.sleep(0.2)
    _send(conn, {"Event": "SettleBegin"})
    time.sleep(0.2)
    _send(conn, {"Event": "SettleDone", "Status": 0, "TotalFrames": 10, "DroppedFrames": 0})


responder2 = threading.Thread(target=_respond_guide_success, daemon=True)
responder2.start()
settled = client.guide_and_wait_for_settle(settle_timeout=5.0)
responder2.join(timeout=3.0)
check("guide_and_wait_for_settle returns True on Status=0", settled is True)

# --- Test 3: guide_and_wait_for_settle failure path ---
def _respond_guide_failure():
    reqs = _recv_lines(conn, 1)
    req_id = reqs[0]["id"]
    _send(conn, {"jsonrpc": "2.0", "result": 0, "id": req_id})
    time.sleep(0.2)
    _send(conn, {"Event": "SettleDone", "Status": 1, "Error": "star lost"})


responder3 = threading.Thread(target=_respond_guide_failure, daemon=True)
responder3.start()
settled2 = client.guide_and_wait_for_settle(settle_timeout=5.0)
responder3.join(timeout=3.0)
check("guide_and_wait_for_settle returns False on Status!=0", settled2 is False)

# --- Test 4: RPC error envelope raises Phd2Error ---
def _respond_error():
    reqs = _recv_lines(conn, 1)
    req_id = reqs[0]["id"]
    _send(conn, {"jsonrpc": "2.0", "error": {"code": 1, "message": "not connected"}, "id": req_id})


responder4 = threading.Thread(target=_respond_error, daemon=True)
responder4.start()
try:
    client.get_connected()
    check("Phd2Error raised on RPC error envelope", False)
except Phd2Error as exc:
    check(f"Phd2Error raised on RPC error envelope ({exc})", True)
responder4.join(timeout=3.0)

client.close()
conn.close()
server_sock.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("All checks passed.")
