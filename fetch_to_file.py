#!/usr/bin/env python3
import urllib.request
import csv
import json
import base64
from pathlib import Path

API_URL = "https://www.vpngate.net/api/iphone/"
OUTPUT_FILE = Path(__file__).resolve().parent / "vpngate_nodes.json"

def fetch_api_text() -> str:
    print(f"Fetching VPNGate nodes from {API_URL}...")
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 vpngate-socks-auth/1.0",
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")

def parse_vpngate_rows(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line and not line.startswith("*")]
    if lines and lines[0].startswith("#"):
        lines[0] = lines[0][1:]
    return list(csv.DictReader(lines))

def main():
    try:
        raw_text = fetch_api_text()
        rows = parse_vpngate_rows(raw_text)
        
        nodes = []
        for row in rows:
            # Clean up base64 config to keep JSON output clean, but let's keep other fields
            ip = row.get("IP", "")
            country_long = row.get("CountryLong", "")
            country_short = row.get("CountryShort", "")
            score = row.get("Score", "")
            ping = row.get("Ping", "")
            speed = row.get("Speed", "")
            uptime = row.get("Uptime", "")
            operator = row.get("Operator", "")
            
            if not ip:
                continue
                
            nodes.append({
                "ip": ip,
                "country_long": country_long,
                "country_short": country_short,
                "score": int(score) if score.isdigit() else 0,
                "ping": int(ping) if ping.isdigit() else -1,
                "speed": int(speed) if speed.isdigit() else 0,
                "uptime": int(uptime) if uptime.isdigit() else 0,
                "operator": operator,
            })
            
        print(f"Successfully parsed {len(nodes)} nodes.")
        
        # Save as JSON list
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(nodes, f, ensure_ascii=False, indent=2)
            
        print(f"Node list written successfully to: {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"Error fetching or writing nodes: {e}")

if __name__ == "__main__":
    main()
