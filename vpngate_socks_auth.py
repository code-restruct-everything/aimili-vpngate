#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.util
import hmac
import os
import queue
import re
import select
import shlex
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vpn_utils

API_URL = os.environ.get("VPNGATE_API_URL", "https://www.vpngate.net/api/iphone/")
OPENVPN_CMD = os.environ.get("OPENVPN_CMD", "openvpn")
OPENVPN_AUTH_USER = os.environ.get("OPENVPN_AUTH_USER", "vpn")
OPENVPN_AUTH_PASS = os.environ.get("OPENVPN_AUTH_PASS", "vpn")
OPENVPN_TEST_TIMEOUT_SECONDS = int(os.environ.get("OPENVPN_TEST_TIMEOUT_SECONDS", "15"))
SOCKS_HOST = os.environ.get("SOCKS_HOST", "0.0.0.0")
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "7928"))
SOCKS_ALLOWED_USERS = tuple(
    user.strip() for user in os.environ.get("SOCKS_ALLOWED_USERS", "").split(",") if user.strip()
)
MAX_SCAN_ROWS = int(os.environ.get("MAX_SCAN_ROWS", "300"))
TEST_CANDIDATES = int(os.environ.get("TEST_CANDIDATES", "8"))
DATA_DIR = Path(os.environ.get("VPNGATE_DATA_DIR", Path(__file__).resolve().parent / "vpngate_data")).resolve()
CONFIG_DIR = DATA_DIR / "configs"
AUTH_FILE = DATA_DIR / "vpngate_auth.txt"

stop_event = threading.Event()
openvpn_lock = threading.RLock()
auth_lock = threading.Lock()
active_openvpn_process: subprocess.Popen[str] | None = None
active_node: "Node | None" = None
libcrypt: ctypes.CDLL | None = None


def _load_libcrypt() -> ctypes.CDLL | None:
    for lib_name in ("crypt", "xcrypt", "c"):
        lib_path = ctypes.util.find_library(lib_name)
        if not lib_path:
            continue
        try:
            lib = ctypes.CDLL(lib_path)
            crypt_fn = getattr(lib, "crypt", None)
            if crypt_fn is None:
                continue
            crypt_fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            crypt_fn.restype = ctypes.c_char_p
            return lib
        except Exception:
            continue
    return None


libcrypt = _load_libcrypt()


@dataclass
class Node:
    id: str
    country: str
    ip: str
    score: int
    ping: int
    config_text: str
    config_file: Path
    remote_host: str
    remote_port: int
    proto: str


def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def parse_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("._") or "node"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    CONFIG_DIR.mkdir(exist_ok=True, parents=True)
    if not AUTH_FILE.exists():
        AUTH_FILE.write_text(f"{OPENVPN_AUTH_USER}\n{OPENVPN_AUTH_PASS}\n", encoding="utf-8")
        try:
            AUTH_FILE.chmod(0o600)
        except OSError:
            pass


def fetch_api_text() -> str:
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 vpngate-socks-auth/1.0",
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_vpngate_rows(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line and not line.startswith("*")]
    if lines and lines[0].startswith("#"):
        lines[0] = lines[0][1:]
    return list(csv.DictReader(lines))


def decode_config(encoded: str) -> str:
    return base64.b64decode(encoded.encode("ascii"), validate=False).decode("utf-8", errors="replace")


def row_to_node(row: dict[str, str], config_text: str) -> Node:
    ip = row.get("IP", "")
    country_short = row.get("CountryShort", "")
    remote_host, remote_port, proto = vpn_utils.parse_remote(config_text, ip)
    node_id = safe_name("_".join([country_short or "XX", ip or remote_host, str(remote_port), proto]))
    config_path = CONFIG_DIR / f"{node_id}.ovpn"
    country_long = row.get("CountryLong", "")
    country_zh = vpn_utils.COUNTRY_TRANSLATIONS.get(country_long, country_long)
    return Node(
        id=node_id,
        country=country_zh,
        ip=ip,
        score=parse_int(row.get("Score")),
        ping=parse_int(row.get("Ping")),
        config_text=config_text,
        config_file=config_path,
        remote_host=remote_host,
        remote_port=remote_port,
        proto=proto,
    )


def fetch_candidates() -> list[Node]:
    api_text = fetch_api_text()
    rows = parse_vpngate_rows(api_text)
    candidates: list[Node] = []
    seen_ips: set[str] = set()
    for row in rows[:MAX_SCAN_ROWS]:
        ip = row.get("IP", "")
        if not ip or ip in seen_ips:
            continue
        encoded = row.get("OpenVPN_ConfigData_Base64", "")
        if not encoded:
            continue
        config_text = decode_config(encoded)
        candidates.append(row_to_node(row, config_text))
        seen_ips.add(ip)
    candidates.sort(key=lambda n: (-n.score, n.ping if n.ping > 0 else 999999))
    return candidates


def get_openvpn_version() -> float:
    try:
        cmd = shlex.split(OPENVPN_CMD, posix=False) or ["openvpn"]
        res = subprocess.run([cmd[0], "--version"], capture_output=True, text=True, timeout=2)
        match = re.search(r"OpenVPN\s+(\d+\.\d+)", (res.stdout or "") + (res.stderr or ""))
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return 2.4


def openvpn_command(config_file: str, route_nopull: bool, dev: str) -> list[str]:
    command = shlex.split(OPENVPN_CMD, posix=False) or ["openvpn"]
    command.extend(
        [
            "--config",
            config_file,
            "--dev",
            dev,
            "--dev-type",
            "tun",
            "--pull-filter",
            "ignore",
            "route-ipv6",
            "--pull-filter",
            "ignore",
            "ifconfig-ipv6",
            "--route-delay",
            "2",
            "--connect-retry-max",
            "1",
            "--connect-timeout",
            "15",
            "--auth-user-pass",
            str(AUTH_FILE),
            "--auth-nocache",
            "--verb",
            "3",
        ]
    )
    version = get_openvpn_version()
    if version >= 2.5:
        command.extend(["--data-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"])
    else:
        command.extend(["--ncp-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"])
    if route_nopull:
        command.append("--route-nopull")
    return command


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()


def run_openvpn_until_ready(
    config_file: str,
    keep_alive: bool,
    route_nopull: bool,
    timeout: int,
    dev: str,
) -> tuple[bool, str, subprocess.Popen[str] | None]:
    try:
        process = subprocess.Popen(
            openvpn_command(config_file, route_nopull, dev),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path(__file__).resolve().parent),
        )
    except FileNotFoundError:
        return False, "openvpn command not found", None
    except OSError as exc:
        return False, f"openvpn start failed: {exc}", None

    lines: queue.Queue[str | None] = queue.Queue()
    startup_done = [False]

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if not startup_done[0]:
                lines.put(text)
            elif keep_alive:
                log(f"[OpenVPN] {text}")
        if not startup_done[0]:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    started = time.time()
    tail: list[str] = []
    ok = False
    message = "OpenVPN did not complete initialization."
    while time.time() - started < timeout:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if line is None:
            break
        if line:
            tail.append(line)
            tail = tail[-10:]
            if keep_alive:
                log(f"[OpenVPN] {line}")
        lower = (line or "").lower()
        if "initialization sequence completed" in lower:
            ok = True
            elapsed_ms = int((time.time() - started) * 1000)
            message = f"OpenVPN connected in {elapsed_ms} ms."
            break
        if "auth_failed" in lower or "authentication failed" in lower:
            message = "AUTH_FAILED"
            break
        if "fatal error" in lower:
            message = line[-220:]
            break

    if not ok and tail:
        message = tail[-1][-220:]

    startup_done[0] = True
    if not keep_alive or not ok:
        stop_process(process)
        process = None
    return ok, message, process


def setup_policy_routing(interface: str = "tun0") -> None:
    try:
        subprocess.run(["ip", "rule", "del", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.run(["ip", "route", "flush", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass
    subprocess.run(["ip", "route", "add", "default", "dev", interface, "table", "100"], check=True, timeout=2)
    subprocess.run(["ip", "rule", "add", "oif", interface, "table", "100"], check=True, timeout=2)


def cleanup_policy_routing() -> None:
    try:
        subprocess.run(["ip", "rule", "del", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.run(["ip", "route", "flush", "table", "100"], capture_output=True, timeout=2)
    except Exception:
        pass


def kill_existing_openvpn_processes() -> None:
    if not sys_platform_linux():
        return
    try:
        subprocess.run(["pkill", "-f", "openvpn.*tun0"], capture_output=True, timeout=2)
    except Exception:
        pass


def sys_platform_linux() -> bool:
    return os.name == "posix" and Path("/sys/class/net").exists()


def connect_candidate(node: Node, dev: str, keep_alive: bool, timeout: int) -> tuple[bool, str, subprocess.Popen[str] | None]:
    node.config_file.write_text(node.config_text, encoding="utf-8")
    return run_openvpn_until_ready(str(node.config_file), keep_alive, route_nopull=True, timeout=timeout, dev=dev)


def pick_best_node(candidates: list[Node]) -> Node:
    tested: list[tuple[Node, int]] = []
    for idx, node in enumerate(candidates[: max(1, TEST_CANDIDATES)]):
        test_dev = f"tun{idx + 2}"
        log(f"Testing candidate {idx + 1}/{min(len(candidates), TEST_CANDIDATES)}: {node.id}")
        latency = vpn_utils.ping_latency_ms(node.ip or node.remote_host, node.remote_port, node.ping)
        ok, msg, _ = connect_candidate(node, dev=test_dev, keep_alive=False, timeout=OPENVPN_TEST_TIMEOUT_SECONDS)
        if ok:
            tested.append((node, latency if latency > 0 else 999999))
            log(f"Candidate available: {node.id} ({latency} ms)")
        else:
            log(f"Candidate unavailable: {node.id} ({msg})")
        try:
            if node.config_file.exists():
                node.config_file.unlink()
        except Exception:
            pass
    if not tested:
        raise RuntimeError("No usable VPNGate node found after testing.")
    tested.sort(key=lambda item: (item[1], -item[0].score))
    return tested[0][0]


def activate_node(node: Node) -> None:
    global active_openvpn_process, active_node
    with openvpn_lock:
        cleanup_policy_routing()
        stop_process(active_openvpn_process)
        active_openvpn_process = None
        active_node = None
        kill_existing_openvpn_processes()

        ok, msg, process = connect_candidate(node, dev="tun0", keep_alive=True, timeout=max(25, OPENVPN_TEST_TIMEOUT_SECONDS))
        if not ok or process is None:
            raise RuntimeError(f"Failed to connect {node.id}: {msg}")
        active_openvpn_process = process
        active_node = node
        setup_policy_routing("tun0")
        log(f"Connected node: {node.id}")


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Unexpected disconnect.")
        data += chunk
    return data


def _read_shadow_hash(username: str) -> str | None:
    try:
        with open("/etc/shadow", "r", encoding="utf-8", errors="replace") as shadow_file:
            for line in shadow_file:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if parts and parts[0] == username:
                    if len(parts) < 2:
                        return None
                    stored_hash = parts[1].strip()
                    if not stored_hash or stored_hash in {"x", "*", "!"}:
                        return None
                    if stored_hash.startswith("!") or stored_hash.startswith("*"):
                        return None
                    return stored_hash
    except Exception:
        return None
    return None


def _crypt_password(password: str, salt_or_hash: str) -> str | None:
    if libcrypt is None:
        return None
    try:
        with auth_lock:
            crypt_fn = getattr(libcrypt, "crypt")
            result = crypt_fn(password.encode("utf-8"), salt_or_hash.encode("utf-8"))
        if not result:
            return None
        return result.decode("utf-8", errors="replace")
    except Exception:
        return None


def verify_proxy_credentials(username: str, password: str) -> bool:
    if SOCKS_ALLOWED_USERS and username not in SOCKS_ALLOWED_USERS:
        return False

    stored_hash = _read_shadow_hash(username)
    if not stored_hash:
        return False

    calculated_hash = _crypt_password(password, stored_hash)
    if not calculated_hash:
        return False

    return hmac.compare_digest(calculated_hash, stored_hash)


def resolve_dns_over_tun0(host: str, dns_server: str = "8.8.8.8", timeout: float = 3.0) -> str | None:
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass

    import random

    tx_id = random.getrandbits(16).to_bytes(2, "big")
    flags = b"\x01\x00"
    questions = b"\x00\x01"
    rrs = b"\x00\x00\x00\x00\x00\x00"

    qname = b""
    for part in host.split("."):
        if not part:
            continue
        part_bytes = part.encode("idna")
        qname += len(part_bytes).to_bytes(1, "big") + part_bytes
    qname += b"\x00"

    qtype_qclass = b"\x00\x01\x00\x01"
    packet = tx_id + flags + questions + rrs + qname + qtype_qclass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"tun0")
        sock.sendto(packet, (dns_server, 53))
        resp, _ = sock.recvfrom(2048)
    except Exception:
        return None
    finally:
        sock.close()

    if len(resp) < 12 or resp[:2] != tx_id:
        return None
    if (resp[3] & 0x0F) != 0:
        return None

    offset = 12
    while offset < len(resp):
        length = resp[offset]
        if length == 0:
            offset += 1
            break
        if (length & 0xC0) == 0xC0:
            offset += 2
            break
        offset += 1 + length

    offset += 4
    answers_count = int.from_bytes(resp[6:8], "big")
    for _ in range(answers_count):
        if offset >= len(resp):
            break
        if (resp[offset] & 0xC0) == 0xC0:
            offset += 2
        else:
            while offset < len(resp) and resp[offset] != 0:
                offset += 1 + resp[offset]
            offset += 1
        if offset + 10 > len(resp):
            break
        atype = int.from_bytes(resp[offset : offset + 2], "big")
        aclass = int.from_bytes(resp[offset + 2 : offset + 4], "big")
        rdlength = int.from_bytes(resp[offset + 8 : offset + 10], "big")
        offset += 10
        if offset + rdlength > len(resp):
            break
        if atype == 1 and aclass == 1 and rdlength == 4:
            return socket.inet_ntoa(resp[offset : offset + 4])
        offset += rdlength
    return None


def create_connection_via_tun0(host: str, port: int, timeout: float = 20.0) -> socket.socket:
    resolved = resolve_dns_over_tun0(host)
    if resolved:
        host = resolved

    last_err: OSError | None = None
    for af, socktype, proto, _, sa in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.settimeout(timeout)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"tun0")
            sock.connect(sa)
            return sock
        except OSError as exc:
            last_err = exc
            if sock is not None:
                sock.close()
    if last_err:
        raise last_err
    raise OSError("getaddrinfo returned no route")


def relay(a: socket.socket, b: socket.socket) -> None:
    sockets = [a, b]
    while not stop_event.is_set():
        readable, _, errored = select.select(sockets, [], sockets, 120)
        if errored:
            return
        for src in readable:
            dst = b if src is a else a
            data = src.recv(65536)
            if not data:
                return
            dst.sendall(data)


def socks5_reply(client: socket.socket, rep: int, bind_host: str = "0.0.0.0", bind_port: int = 0) -> None:
    try:
        packed_ip = socket.inet_aton(bind_host)
        atyp = 1
    except OSError:
        packed_ip = b"\x00\x00\x00\x00"
        atyp = 1
    resp = b"\x05" + bytes([rep]) + b"\x00" + bytes([atyp]) + packed_ip + bind_port.to_bytes(2, "big")
    client.sendall(resp)


def handle_socks_client(client: socket.socket, address: tuple[str, int]) -> None:
    upstream = None
    try:
        client.settimeout(30)
        head = recv_exact(client, 2)
        if head[0] != 5:
            return
        nmethods = head[1]
        methods = recv_exact(client, nmethods)
        if 2 not in methods:
            client.sendall(b"\x05\xff")
            return
        client.sendall(b"\x05\x02")

        auth_ver = recv_exact(client, 1)[0]
        if auth_ver != 1:
            client.sendall(b"\x01\x01")
            return
        ulen = recv_exact(client, 1)[0]
        uname = recv_exact(client, ulen).decode("utf-8", errors="replace")
        plen = recv_exact(client, 1)[0]
        passwd = recv_exact(client, plen).decode("utf-8", errors="replace")
        if not verify_proxy_credentials(uname, passwd):
            client.sendall(b"\x01\x01")
            log(f"SOCKS auth failed from {address[0]}:{address[1]}")
            return
        client.sendall(b"\x01\x00")

        ver, cmd, _, atyp = recv_exact(client, 4)
        if ver != 5 or cmd != 1:
            socks5_reply(client, 7)
            return

        if atyp == 1:
            host = socket.inet_ntoa(recv_exact(client, 4))
        elif atyp == 3:
            host = recv_exact(client, recv_exact(client, 1)[0]).decode("idna")
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        else:
            socks5_reply(client, 8)
            return
        port = int.from_bytes(recv_exact(client, 2), "big")

        try:
            upstream = create_connection_via_tun0(host, port, timeout=20)
        except Exception as exc:
            log(f"Upstream connect failed {host}:{port}: {exc}")
            socks5_reply(client, 4)
            return

        bind_host, bind_port = upstream.getsockname()[:2]
        socks5_reply(client, 0, str(bind_host), int(bind_port))
        relay(client, upstream)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        if upstream is not None:
            try:
                upstream.close()
            except OSError:
                pass


def start_socks_server(host: str, port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(256)
    server.settimeout(1.0)
    log(f"SOCKS5 server listening on {host}:{port} (auth enabled)")
    try:
        while not stop_event.is_set():
            with openvpn_lock:
                proc = active_openvpn_process
            if proc is None or proc.poll() is not None:
                code = proc.poll() if proc is not None else None
                raise RuntimeError(f"OpenVPN process exited unexpectedly (code={code}).")
            try:
                client, addr = server.accept()
                threading.Thread(target=handle_socks_client, args=(client, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.2)
    finally:
        try:
            server.close()
        except OSError:
            pass


def graceful_shutdown(*_: Any) -> None:
    stop_event.set()
    with openvpn_lock:
        cleanup_policy_routing()
        stop_process(active_openvpn_process)
    log("Shutdown complete.")
    raise SystemExit(0)


def require_linux_root() -> None:
    if not sys_platform_linux():
        raise RuntimeError("This script must run on Linux.")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise RuntimeError("This script requires root privileges for tun routing and SO_BINDTODEVICE.")


def main() -> None:
    require_linux_root()
    ensure_dirs()
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    if libcrypt is None:
        raise RuntimeError("System user auth requires libc/libxcrypt crypt() support.")
    if not os.access("/etc/shadow", os.R_OK):
        raise RuntimeError("System user auth requires read access to /etc/shadow (run as root).")
    allowed = ",".join(SOCKS_ALLOWED_USERS) if SOCKS_ALLOWED_USERS else "(all system users)"
    log(f"SOCKS auth mode: system users (allowed users: {allowed})")

    log("Fetching VPNGate candidates...")
    candidates = fetch_candidates()
    if not candidates:
        raise RuntimeError("No candidate nodes fetched from VPNGate API.")

    log(f"Fetched {len(candidates)} candidates, testing top {min(len(candidates), TEST_CANDIDATES)}")
    best = pick_best_node(candidates)
    log(f"Selected best node: {best.id} ({best.remote_host}:{best.remote_port}, score={best.score}, ping={best.ping})")

    activate_node(best)
    start_socks_server(SOCKS_HOST, SOCKS_PORT)


if __name__ == "__main__":
    main()
