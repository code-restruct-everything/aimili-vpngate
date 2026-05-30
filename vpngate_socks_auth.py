#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.util
import hmac
import json
import os
import queue
import re
import select
import shlex
import signal
import ssl
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vpn_utils


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int((raw or "").strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        return float((raw or "").strip())
    except (TypeError, ValueError):
        return default


def _env_csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name, default) or default
    values = []
    for item in raw.split(","):
        token = item.strip().lower()
        if token:
            values.append(token)
    return tuple(values)


def _parse_host_port_list(raw: str, default: str) -> tuple[tuple[str, int], ...]:
    def parse_items(source: str) -> list[tuple[str, int]]:
        parsed: list[tuple[str, int]] = []
        for item in (source or "").split(","):
            token = item.strip()
            if not token or ":" not in token:
                continue
            host, port_text = token.rsplit(":", 1)
            host = host.strip()
            if not host:
                continue
            try:
                port = int(port_text.strip())
            except ValueError:
                continue
            if 1 <= port <= 65535:
                parsed.append((host, port))
        return parsed

    values = parse_items(raw)
    if values:
        return tuple(values)
    return tuple(parse_items(default))


def _parse_speed_test_url_list(raw: str, default: str) -> tuple[tuple[str, int, str, bool], ...]:
    def parse_items(source: str) -> list[tuple[str, int, str, bool]]:
        parsed_items: list[tuple[str, int, str, bool]] = []
        for item in (source or "").split(","):
            token = item.strip()
            if not token:
                continue
            try:
                parsed = urllib.parse.urlsplit(token)
            except Exception:
                continue
            scheme = parsed.scheme.lower()
            if scheme not in {"http", "https"}:
                continue
            host = (parsed.hostname or "").strip()
            if not host:
                continue
            port = parsed.port or (443 if scheme == "https" else 80)
            if not (1 <= port <= 65535):
                continue
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            parsed_items.append((host, port, path, scheme == "https"))
        return parsed_items

    values = parse_items(raw)
    if values:
        return tuple(values)
    return tuple(parse_items(default))


def _parse_country_score_map(raw: str) -> dict[str, int]:
    score_map: dict[str, int] = {}
    for item in (raw or "").split(","):
        token = item.strip()
        if not token or ":" not in token:
            continue
        code, score_text = token.split(":", 1)
        code = code.strip().upper()
        if not code:
            continue
        try:
            score_map[code] = int(score_text.strip())
        except ValueError:
            continue
    return score_map


def _parse_asn_blacklist(raw: str) -> tuple[set[str], set[str]]:
    asn_codes: set[str] = set()
    asn_nums: set[str] = set()
    for item in (raw or "").split(","):
        token = item.strip().upper()
        if not token:
            continue
        match = re.search(r"^(?:AS)?(\d+)$", token)
        if not match:
            continue
        num = match.group(1)
        asn_nums.add(num)
        asn_codes.add(f"AS{num}")
    return asn_codes, asn_nums


API_URL = os.environ.get("VPNGATE_API_URL", "https://www.vpngate.net/api/iphone/")
OPENVPN_CMD = os.environ.get("OPENVPN_CMD", "openvpn")
OPENVPN_AUTH_USER = os.environ.get("OPENVPN_AUTH_USER", "vpn")
OPENVPN_AUTH_PASS = os.environ.get("OPENVPN_AUTH_PASS", "vpn")
OPENVPN_TEST_TIMEOUT_SECONDS = _env_int("OPENVPN_TEST_TIMEOUT_SECONDS", 15)
SOCKS_HOST = os.environ.get("SOCKS_HOST", "0.0.0.0")
SOCKS_PORT = _env_int("SOCKS_PORT", 7928)
SOCKS_ALLOWED_USERS = tuple(
    user.strip() for user in os.environ.get("SOCKS_ALLOWED_USERS", "").split(",") if user.strip()
)
VPNGATE_COUNTRY = os.environ.get("VPNGATE_COUNTRY", "").strip()
VPNGATE_COUNTRY_SHORT = os.environ.get("VPNGATE_COUNTRY_SHORT", "").strip().upper()
VPN_TUN_DEV = os.environ.get("VPN_TUN_DEV", "tun0").strip() or "tun0"
VPN_ROUTE_TABLE = _env_int("VPN_ROUTE_TABLE", 100)
OPENVPN_TEST_DEV = os.environ.get("OPENVPN_TEST_DEV", "tun").strip() or "tun"
MAX_SCAN_ROWS = _env_int("MAX_SCAN_ROWS", 300)
TEST_CANDIDATES = _env_int("TEST_CANDIDATES", 8)
UPSTREAM_FAIL_RESTART_THRESHOLD = _env_int("UPSTREAM_FAIL_RESTART_THRESHOLD", 12)
UPSTREAM_HEALTHCHECK_TARGETS = _parse_host_port_list(
    os.environ.get("UPSTREAM_HEALTHCHECK_TARGETS", ""),
    "www.google.com:443,www.cloudflare.com:443",
)
UPSTREAM_HEALTHCHECK_INTERVAL_SECONDS = max(3, _env_int("UPSTREAM_HEALTHCHECK_INTERVAL_SECONDS", 10))
UPSTREAM_HEALTHCHECK_TIMEOUT_SECONDS = max(1, _env_int("UPSTREAM_HEALTHCHECK_TIMEOUT_SECONDS", 6))
VPNGATE_SPEED_TEST_ENABLE = _env_bool("VPNGATE_SPEED_TEST_ENABLE", True)
VPNGATE_SPEED_TEST_TARGETS = _parse_speed_test_url_list(
    os.environ.get("VPNGATE_SPEED_TEST_TARGETS", ""),
    "https://speed.cloudflare.com/__down?bytes=262144",
)
VPNGATE_SPEED_TEST_TIMEOUT_SECONDS = max(2, _env_int("VPNGATE_SPEED_TEST_TIMEOUT_SECONDS", 8))
VPNGATE_SPEED_TEST_MAX_BYTES = max(32768, _env_int("VPNGATE_SPEED_TEST_MAX_BYTES", 262144))
VPNGATE_RANK_WEIGHT_LATENCY = max(0.0, _env_float("VPNGATE_RANK_WEIGHT_LATENCY", 0.6))
VPNGATE_RANK_WEIGHT_SPEED = max(0.0, _env_float("VPNGATE_RANK_WEIGHT_SPEED", 0.4))
VPNGATE_RISK_ENABLE = _env_bool("VPNGATE_RISK_ENABLE", False)
VPNGATE_RISK_BLOCK_QUALITY = set(_env_csv("VPNGATE_RISK_BLOCK_QUALITY", "proxy,datacenter"))
VPNGATE_RISK_ASN_BLACKLIST_RAW = os.environ.get("VPNGATE_RISK_ASN_BLACKLIST", "")
VPNGATE_RISK_ASN_BLACKLIST_CODES, VPNGATE_RISK_ASN_BLACKLIST_NUMS = _parse_asn_blacklist(
    VPNGATE_RISK_ASN_BLACKLIST_RAW
)
VPNGATE_RISK_GEOIP_THRESHOLD = _env_int("VPNGATE_RISK_GEOIP_THRESHOLD", 70)
VPNGATE_RISK_PROXY_SCORE = _env_int("VPNGATE_RISK_PROXY_SCORE", 80)
VPNGATE_RISK_DATACENTER_SCORE = _env_int("VPNGATE_RISK_DATACENTER_SCORE", 60)
VPNGATE_RISK_MOBILE_SCORE = _env_int("VPNGATE_RISK_MOBILE_SCORE", 20)
VPNGATE_RISK_COUNTRY_SCORE_MAP = _parse_country_score_map(os.environ.get("VPNGATE_RISK_COUNTRY_SCORES", ""))
VPNGATE_RISK_FAIL_OPEN = _env_bool("VPNGATE_RISK_FAIL_OPEN", True)
VPNGATE_RISK_API_URL = os.environ.get(
    "VPNGATE_RISK_API_URL",
    "http://ip-api.com/batch?fields=status,query,countryCode,proxy,hosting,mobile,as,asname",
).strip()
DATA_DIR = Path(os.environ.get("VPNGATE_DATA_DIR", Path(__file__).resolve().parent / "vpngate_data")).resolve()
CONFIG_DIR = DATA_DIR / "configs"
AUTH_FILE = DATA_DIR / "vpngate_auth.txt"

stop_event = threading.Event()
openvpn_lock = threading.RLock()
auth_lock = threading.Lock()
upstream_fail_lock = threading.Lock()
upstream_fail_restart_event = threading.Event()
active_openvpn_process: subprocess.Popen[str] | None = None
active_node: "Node | None" = None
upstream_fail_count = 0
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
    asn: str = ""
    quality: str = ""
    country_code: str = ""
    geoip_risk: int = 0


def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def record_upstream_failure() -> int:
    if UPSTREAM_FAIL_RESTART_THRESHOLD <= 0:
        return 0
    global upstream_fail_count
    with upstream_fail_lock:
        upstream_fail_count += 1
        return upstream_fail_count


def record_upstream_success() -> None:
    if UPSTREAM_FAIL_RESTART_THRESHOLD <= 0:
        return
    global upstream_fail_count
    with upstream_fail_lock:
        upstream_fail_count = 0


def check_upstream_health_target(host: str, port: int) -> bool:
    sock = None
    try:
        sock = create_connection_via_tun(host, port, interface=VPN_TUN_DEV, timeout=UPSTREAM_HEALTHCHECK_TIMEOUT_SECONDS)
        return True
    except Exception as exc:
        log(f"Health check failed {host}:{port}: {exc}")
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def build_test_dev_name(base_dev: str, index: int) -> str:
    if base_dev not in {"tun", "tap"}:
        return base_dev
    candidate = f"{base_dev}probe{index + 1}"
    return candidate[:15]


def probe_download_speed_bps(interface: str, host: str, port: int, path: str, use_tls: bool) -> int:
    conn = None
    try:
        conn = create_connection_via_tun(host, port, interface=interface, timeout=VPNGATE_SPEED_TEST_TIMEOUT_SECONDS)
        if use_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            conn = context.wrap_socket(conn, server_hostname=host)
        conn.settimeout(VPNGATE_SPEED_TEST_TIMEOUT_SECONDS)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: vpngate-speed-probe/1.0\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii", errors="ignore")
        conn.sendall(request)

        started = time.time()
        buffered = b""
        header_done = False
        body_bytes = 0
        while body_bytes < VPNGATE_SPEED_TEST_MAX_BYTES:
            chunk = conn.recv(65536)
            if not chunk:
                break
            if not header_done:
                buffered += chunk
                split_at = buffered.find(b"\r\n\r\n")
                if split_at < 0:
                    if len(buffered) > 131072:
                        return 0
                    continue
                header_done = True
                body = buffered[split_at + 4 :]
                body_bytes += len(body)
                buffered = b""
            else:
                body_bytes += len(chunk)

        elapsed = max(0.001, time.time() - started)
        if body_bytes <= 0:
            return 0
        return int(body_bytes / elapsed)
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def probe_candidate_speed_bps(interface: str) -> int:
    if not VPNGATE_SPEED_TEST_ENABLE or not VPNGATE_SPEED_TEST_TARGETS:
        return 0
    for host, port, path, use_tls in VPNGATE_SPEED_TEST_TARGETS:
        speed_bps = probe_download_speed_bps(interface, host, port, path, use_tls)
        if speed_bps > 0:
            return speed_bps
    return 0


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


def _extract_asn_tokens(raw_asn: str) -> tuple[str, str]:
    upper = (raw_asn or "").upper()
    matched = re.search(r"\bAS(\d+)\b", upper)
    if matched:
        num = matched.group(1)
        return f"AS{num}", num
    matched = re.search(r"\b(\d+)\b", upper)
    if matched:
        num = matched.group(1)
        return f"AS{num}", num
    return "", ""


def query_risk_metadata(candidates: list[Node]) -> dict[str, dict[str, Any]]:
    ips: list[str] = []
    seen: set[str] = set()
    for node in candidates:
        ip = (node.ip or node.remote_host or "").strip()
        if not ip or ip in seen:
            continue
        seen.add(ip)
        ips.append(ip)

    if not ips:
        return {}

    metadata: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ips), 100):
        chunk = ips[i : i + 100]
        request = urllib.request.Request(
            VPNGATE_RISK_API_URL,
            data=json.dumps(chunk).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "vpngate-socks-auth/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read().decode("utf-8", errors="replace")
            items = json.loads(payload)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("status") != "success":
                    continue
                ip = str(item.get("query") or "").strip()
                if not ip:
                    continue

                is_proxy = bool(item.get("proxy"))
                is_hosting = bool(item.get("hosting"))
                is_mobile = bool(item.get("mobile"))
                quality = "normal"
                if is_proxy:
                    quality = "proxy"
                elif is_hosting:
                    quality = "datacenter"
                elif is_mobile:
                    quality = "mobile"

                asn_raw = str(item.get("as") or "").strip()
                asn_code, asn_num = _extract_asn_tokens(asn_raw)
                country_code = str(item.get("countryCode") or "").strip().upper()

                risk_score = 0
                if is_proxy:
                    risk_score += VPNGATE_RISK_PROXY_SCORE
                if is_hosting:
                    risk_score += VPNGATE_RISK_DATACENTER_SCORE
                if is_mobile:
                    risk_score += VPNGATE_RISK_MOBILE_SCORE
                if country_code:
                    risk_score += VPNGATE_RISK_COUNTRY_SCORE_MAP.get(country_code, 0)

                metadata[ip] = {
                    "quality": quality,
                    "asn_code": asn_code,
                    "asn_num": asn_num,
                    "country_code": country_code,
                    "risk_score": risk_score,
                }
    return metadata


def apply_risk_filters(candidates: list[Node]) -> list[Node]:
    if not VPNGATE_RISK_ENABLE or not candidates:
        return candidates

    try:
        meta_by_ip = query_risk_metadata(candidates)
    except Exception as exc:
        if VPNGATE_RISK_FAIL_OPEN:
            log(f"Risk metadata query failed, fail-open enabled: {exc}")
            return candidates
        raise RuntimeError(f"Risk metadata query failed: {exc}")

    filtered: list[Node] = []
    blocked: list[str] = []
    missing_meta = 0

    for node in candidates:
        ip = (node.ip or node.remote_host or "").strip()
        meta = meta_by_ip.get(ip)
        reasons: list[str] = []

        if not meta:
            if VPNGATE_RISK_FAIL_OPEN:
                missing_meta += 1
                filtered.append(node)
                continue
            reasons.append("geoip_unavailable")
        else:
            quality = str(meta.get("quality") or "")
            asn_code = str(meta.get("asn_code") or "")
            asn_num = str(meta.get("asn_num") or "")
            country_code = str(meta.get("country_code") or "")
            risk_score = parse_int(meta.get("risk_score"))

            node.quality = quality
            node.asn = asn_code
            node.country_code = country_code
            node.geoip_risk = risk_score

            if quality and quality.lower() in VPNGATE_RISK_BLOCK_QUALITY:
                reasons.append(f"quality={quality}")
            if asn_code in VPNGATE_RISK_ASN_BLACKLIST_CODES or (asn_num and asn_num in VPNGATE_RISK_ASN_BLACKLIST_NUMS):
                reasons.append(f"asn={asn_code or asn_num}")
            if VPNGATE_RISK_GEOIP_THRESHOLD >= 0 and risk_score >= VPNGATE_RISK_GEOIP_THRESHOLD:
                reasons.append(f"risk={risk_score}")

        if reasons:
            blocked.append(f"{node.id}({','.join(reasons)})")
            continue
        filtered.append(node)

    if blocked:
        preview = "; ".join(blocked[:10])
        if len(blocked) > 10:
            preview += "; ..."
        log(f"Risk filter removed {len(blocked)} candidate(s); kept {len(filtered)}. {preview}")
    if missing_meta:
        log(f"Risk metadata missing for {missing_meta} candidate(s), kept due to fail-open mode.")

    return filtered


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


def row_matches_country_filter(row: dict[str, str]) -> bool:
    if VPNGATE_COUNTRY_SHORT:
        row_short = (row.get("CountryShort", "") or "").strip().upper()
        if row_short != VPNGATE_COUNTRY_SHORT:
            return False

    if VPNGATE_COUNTRY:
        row_long = (row.get("CountryLong", "") or "").strip()
        row_short = (row.get("CountryShort", "") or "").strip()
        row_zh = vpn_utils.COUNTRY_TRANSLATIONS.get(row_long, vpn_utils.COUNTRY_TRANSLATIONS.get(row_long.strip(), row_long))
        target = VPNGATE_COUNTRY.lower()
        candidates = {row_long.lower(), row_short.lower(), (row_zh or "").lower()}
        if target not in candidates:
            return False

    return True


def fetch_candidates() -> list[Node]:
    api_text = fetch_api_text()
    rows = parse_vpngate_rows(api_text)
    candidates: list[Node] = []
    seen_ips: set[str] = set()
    for row in rows[:MAX_SCAN_ROWS]:
        ip = row.get("IP", "")
        if not ip or ip in seen_ips:
            continue
        if not row_matches_country_filter(row):
            continue
        encoded = row.get("OpenVPN_ConfigData_Base64", "")
        if not encoded:
            continue
        config_text = decode_config(encoded)
        candidates.append(row_to_node(row, config_text))
        seen_ips.add(ip)
    candidates.sort(key=lambda n: (-n.score, n.ping if n.ping > 0 else 999999))
    candidates = apply_risk_filters(candidates)
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


def setup_policy_routing(interface: str, table_id: int) -> None:
    try:
        subprocess.run(["ip", "rule", "del", "oif", interface, "table", str(table_id)], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.run(["ip", "route", "flush", "table", str(table_id)], capture_output=True, timeout=2)
    except Exception:
        pass
    subprocess.run(
        ["ip", "route", "add", "default", "dev", interface, "table", str(table_id)],
        check=True,
        timeout=2,
    )
    subprocess.run(["ip", "rule", "add", "oif", interface, "table", str(table_id)], check=True, timeout=2)


def cleanup_policy_routing(interface: str, table_id: int) -> None:
    try:
        subprocess.run(["ip", "rule", "del", "oif", interface, "table", str(table_id)], capture_output=True, timeout=2)
    except Exception:
        pass
    try:
        subprocess.run(["ip", "route", "flush", "table", str(table_id)], capture_output=True, timeout=2)
    except Exception:
        pass


def kill_existing_openvpn_processes(interface: str) -> None:
    if not sys_platform_linux():
        return
    try:
        pattern = f"openvpn.*--dev[[:space:]]+{interface}([[:space:]]|$)"
        subprocess.run(
            ["pkill", "-f", pattern],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass


def sys_platform_linux() -> bool:
    return os.name == "posix" and Path("/sys/class/net").exists()


def connect_candidate(node: Node, dev: str, keep_alive: bool, timeout: int) -> tuple[bool, str, subprocess.Popen[str] | None]:
    node.config_file.write_text(node.config_text, encoding="utf-8")
    return run_openvpn_until_ready(str(node.config_file), keep_alive, route_nopull=True, timeout=timeout, dev=dev)


def pick_best_node(candidates: list[Node]) -> Node:
    tested: list[tuple[Node, int, int]] = []
    speed_probe_enabled = VPNGATE_SPEED_TEST_ENABLE and bool(VPNGATE_SPEED_TEST_TARGETS)
    for idx, node in enumerate(candidates[: max(1, TEST_CANDIDATES)]):
        test_dev = build_test_dev_name(OPENVPN_TEST_DEV, idx) if speed_probe_enabled else OPENVPN_TEST_DEV
        log(f"Testing candidate {idx + 1}/{min(len(candidates), TEST_CANDIDATES)}: {node.id}")
        latency = vpn_utils.ping_latency_ms(node.ip or node.remote_host, node.remote_port, node.ping)
        ok, msg, process = connect_candidate(
            node,
            dev=test_dev,
            keep_alive=speed_probe_enabled,
            timeout=OPENVPN_TEST_TIMEOUT_SECONDS,
        )
        if ok:
            speed_bps = probe_candidate_speed_bps(test_dev) if speed_probe_enabled else 0
            tested.append((node, latency if latency > 0 else 999999, speed_bps))
            if speed_probe_enabled:
                speed_kib = int(speed_bps / 1024) if speed_bps > 0 else 0
                log(f"Candidate available: {node.id} ({latency} ms, speed {speed_kib} KiB/s)")
            else:
                log(f"Candidate available: {node.id} ({latency} ms)")
        else:
            log(f"Candidate unavailable: {node.id} ({msg})")
        if process is not None:
            stop_process(process)
        try:
            if node.config_file.exists():
                node.config_file.unlink()
        except Exception:
            pass
    if not tested:
        raise RuntimeError("No usable VPNGate node found after testing.")
    if speed_probe_enabled:
        latency_weight = VPNGATE_RANK_WEIGHT_LATENCY
        speed_weight = VPNGATE_RANK_WEIGHT_SPEED
        if latency_weight <= 0 and speed_weight <= 0:
            latency_weight, speed_weight = 0.6, 0.4
        weight_sum = latency_weight + speed_weight
        latency_weight /= weight_sum
        speed_weight /= weight_sum

        latencies = [item[1] for item in tested]
        speeds = [item[2] for item in tested]
        min_latency, max_latency = min(latencies), max(latencies)
        min_speed, max_speed = min(speeds), max(speeds)

        ranked: list[tuple[float, Node, int, int]] = []
        for node, latency, speed_bps in tested:
            if max_latency == min_latency:
                latency_cost = 0.0
            else:
                latency_cost = (latency - min_latency) / (max_latency - min_latency)
            if max_speed == min_speed:
                speed_cost = 0.0
            else:
                speed_cost = (max_speed - speed_bps) / (max_speed - min_speed)
            weighted_cost = latency_weight * latency_cost + speed_weight * speed_cost
            ranked.append((weighted_cost, node, latency, speed_bps))

        ranked.sort(key=lambda item: (item[0], item[2], -item[3], -item[1].score))
        return ranked[0][1]
    else:
        tested.sort(key=lambda item: (item[1], -item[0].score))
    return tested[0][0]


def activate_node(node: Node) -> None:
    global active_openvpn_process, active_node
    with openvpn_lock:
        cleanup_policy_routing(VPN_TUN_DEV, VPN_ROUTE_TABLE)
        stop_process(active_openvpn_process)
        active_openvpn_process = None
        active_node = None
        kill_existing_openvpn_processes(VPN_TUN_DEV)

        ok, msg, process = connect_candidate(
            node,
            dev=VPN_TUN_DEV,
            keep_alive=True,
            timeout=max(25, OPENVPN_TEST_TIMEOUT_SECONDS),
        )
        if not ok or process is None:
            raise RuntimeError(f"Failed to connect {node.id}: {msg}")
        active_openvpn_process = process
        active_node = node
        setup_policy_routing(VPN_TUN_DEV, VPN_ROUTE_TABLE)
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


def resolve_dns_over_tun(host: str, interface: str, dns_server: str = "8.8.8.8", timeout: float = 3.0) -> str | None:
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
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode("utf-8"))
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


def create_connection_via_tun(host: str, port: int, interface: str, timeout: float = 20.0) -> socket.socket:
    resolved = resolve_dns_over_tun(host, interface)
    if resolved:
        host = resolved

    last_err: OSError | None = None
    for af, socktype, proto, _, sa in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            sock.settimeout(timeout)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode("utf-8"))
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
            recv_exact(client, 16)
            socks5_reply(client, 8)
            return
        else:
            socks5_reply(client, 8)
            return
        port = int.from_bytes(recv_exact(client, 2), "big")

        try:
            upstream = create_connection_via_tun(host, port, interface=VPN_TUN_DEV, timeout=20)
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
    health_index = 0
    next_healthcheck_at = time.monotonic()
    try:
        while not stop_event.is_set():
            with openvpn_lock:
                proc = active_openvpn_process
            if proc is None or proc.poll() is not None:
                code = proc.poll() if proc is not None else None
                raise RuntimeError(f"OpenVPN process exited unexpectedly (code={code}).")
            if UPSTREAM_FAIL_RESTART_THRESHOLD > 0 and UPSTREAM_HEALTHCHECK_TARGETS:
                now = time.monotonic()
                if now >= next_healthcheck_at:
                    target = UPSTREAM_HEALTHCHECK_TARGETS[health_index % len(UPSTREAM_HEALTHCHECK_TARGETS)]
                    health_index += 1
                    next_healthcheck_at = now + UPSTREAM_HEALTHCHECK_INTERVAL_SECONDS
                    if check_upstream_health_target(target[0], target[1]):
                        record_upstream_success()
                    else:
                        fail_count = record_upstream_failure()
                        if fail_count >= UPSTREAM_FAIL_RESTART_THRESHOLD:
                            if not upstream_fail_restart_event.is_set():
                                log(
                                    "Health check consecutive failures reached threshold "
                                    f"({fail_count}/{UPSTREAM_FAIL_RESTART_THRESHOLD}); requesting service restart."
                                )
                            upstream_fail_restart_event.set()
            if upstream_fail_restart_event.is_set():
                raise RuntimeError(
                    "Health check failures reached threshold; restarting for node reselect."
                )
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
        cleanup_policy_routing(VPN_TUN_DEV, VPN_ROUTE_TABLE)
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
    if VPN_ROUTE_TABLE <= 0:
        raise RuntimeError("VPN_ROUTE_TABLE must be a positive integer.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", VPN_TUN_DEV):
        raise RuntimeError("VPN_TUN_DEV contains invalid characters.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", OPENVPN_TEST_DEV):
        raise RuntimeError("OPENVPN_TEST_DEV contains invalid characters.")
    ensure_dirs()
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    if libcrypt is None:
        raise RuntimeError("System user auth requires libc/libxcrypt crypt() support.")
    if not os.access("/etc/shadow", os.R_OK):
        raise RuntimeError("System user auth requires read access to /etc/shadow (run as root).")
    allowed = ",".join(SOCKS_ALLOWED_USERS) if SOCKS_ALLOWED_USERS else "(all system users)"
    log(f"SOCKS auth mode: system users (allowed users: {allowed})")
    log(f"Runtime network: tun={VPN_TUN_DEV}, route_table={VPN_ROUTE_TABLE}, test_dev={OPENVPN_TEST_DEV}")
    if VPNGATE_SPEED_TEST_ENABLE and VPNGATE_SPEED_TEST_TARGETS:
        speed_targets = ",".join(
            f"{'https' if use_tls else 'http'}://{host}:{port}{path}" for host, port, path, use_tls in VPNGATE_SPEED_TEST_TARGETS
        )
        log(
            "Speed probe: enabled "
            f"(targets={speed_targets}, timeout={VPNGATE_SPEED_TEST_TIMEOUT_SECONDS}s, "
            f"max_bytes={VPNGATE_SPEED_TEST_MAX_BYTES}, "
            f"rank_weights=latency:{VPNGATE_RANK_WEIGHT_LATENCY},speed:{VPNGATE_RANK_WEIGHT_SPEED})."
        )
    else:
        log("Speed probe: disabled.")
    if UPSTREAM_FAIL_RESTART_THRESHOLD > 0:
        targets_text = ",".join(f"{item[0]}:{item[1]}" for item in UPSTREAM_HEALTHCHECK_TARGETS) or "-"
        log(
            "Upstream failover: restart on "
            f"{UPSTREAM_FAIL_RESTART_THRESHOLD} consecutive health-check failures "
            f"(targets={targets_text}, interval={UPSTREAM_HEALTHCHECK_INTERVAL_SECONDS}s, "
            f"timeout={UPSTREAM_HEALTHCHECK_TIMEOUT_SECONDS}s)."
        )
    else:
        log("Upstream failover: disabled (UPSTREAM_FAIL_RESTART_THRESHOLD <= 0).")
    if VPNGATE_COUNTRY or VPNGATE_COUNTRY_SHORT:
        log(f"VPNGate country filter: country={VPNGATE_COUNTRY or '-'}, country_short={VPNGATE_COUNTRY_SHORT or '-'}")
    if VPNGATE_RISK_ENABLE:
        quality_rules = ",".join(sorted(VPNGATE_RISK_BLOCK_QUALITY)) or "-"
        asn_rules = ",".join(sorted(VPNGATE_RISK_ASN_BLACKLIST_CODES)) or "-"
        country_rules = ",".join(
            f"{code}:{score}" for code, score in sorted(VPNGATE_RISK_COUNTRY_SCORE_MAP.items())
        ) or "-"
        log(
            "Risk filter enabled: "
            f"block_quality={quality_rules}, "
            f"asn_blacklist={asn_rules}, "
            f"geoip_threshold={VPNGATE_RISK_GEOIP_THRESHOLD}, "
            f"country_scores={country_rules}, "
            f"fail_open={VPNGATE_RISK_FAIL_OPEN}"
        )

    log("Fetching VPNGate candidates...")
    candidates = fetch_candidates()
    if not candidates:
        raise RuntimeError("No candidate nodes fetched from VPNGate API.")

    log(f"Fetched {len(candidates)} candidates, testing top {min(len(candidates), TEST_CANDIDATES)}")
    best = pick_best_node(candidates)
    log(
        f"Selected best node: {best.id} ({best.remote_host}:{best.remote_port}, "
        f"score={best.score}, ping={best.ping}, quality={best.quality or '-'}, "
        f"asn={best.asn or '-'}, geoip_risk={best.geoip_risk})"
    )

    activate_node(best)
    start_socks_server(SOCKS_HOST, SOCKS_PORT)


if __name__ == "__main__":
    main()
