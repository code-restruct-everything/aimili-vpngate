#!/usr/bin/env bash
set -euo pipefail

# 用法:
#   bash dns_pick_best.sh
#   bash dns_pick_best.sh www.google.com 4 4
#   bash dns_pick_best.sh www.google.com 4 4 1.1.1.1 8.8.8.8 9.9.9.9
#
# 参数:
#   $1 domain      默认 www.google.com
#   $2 rounds      每个DNS测几轮，默认 4
#   $3 ping_count  每轮ping几次，默认 4
#   $4...          DNS列表，留空用内置列表

DOMAIN="${1:-www.google.com}"; [ $# -ge 1 ] && shift
ROUNDS="${1:-4}"; [ $# -ge 1 ] && shift
PING_COUNT="${1:-4}"; [ $# -ge 1 ] && shift

if [ "$#" -gt 0 ]; then
  DNS_SERVERS=("$@")
else
  DNS_SERVERS=(1.1.1.1 8.8.8.8 9.9.9.9 208.67.222.222 223.5.5.5 114.114.114.114)
fi

for c in dig ping awk sort; do
  command -v "$c" >/dev/null 2>&1 || { echo "缺少命令: $c"; exit 1; }
done

[[ "$ROUNDS" =~ ^[0-9]+$ ]] || { echo "rounds 必须是整数"; exit 1; }
[[ "$PING_COUNT" =~ ^[0-9]+$ ]] || { echo "ping_count 必须是整数"; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

for dns in "${DNS_SERVERS[@]}"; do
  ok=0
  sum_q=0
  sum_p=0
  sample_ip="-"

  for ((i=1; i<=ROUNDS; i++)); do
    out="$(dig @"$dns" "$DOMAIN" A +time=2 +tries=1 +noall +answer +stats 2>/dev/null || true)"
    ip="$(awk '/\tA\t/ {print $5; exit}' <<<"$out")"
    qms="$(awk -F': ' '/Query time:/ {gsub(/[^0-9.]/,"",$2); print $2; exit}' <<<"$out")"

    [ -n "$ip" ] || continue
    [ -n "$qms" ] || continue

    pavg="$(ping -4 -n -c "$PING_COUNT" -W 1 "$ip" 2>/dev/null | awk -F'=' '/rtt|round-trip/ {split($2,a,"/"); gsub(/^[ \t]+/,"",a[2]); print a[2]; exit}')"
    [ -n "$pavg" ] || continue

    sum_q="$(awk -v a="$sum_q" -v b="$qms" 'BEGIN{printf "%.6f",a+b}')"
    sum_p="$(awk -v a="$sum_p" -v b="$pavg" 'BEGIN{printf "%.6f",a+b}')"
    sample_ip="$ip"
    ok=$((ok+1))
  done

  if [ "$ok" -eq 0 ]; then
    printf "%s\t0\t-\t-\t999999\t-\n" "$dns" >> "$tmp"
    continue
  fi

  avg_q="$(awk -v s="$sum_q" -v n="$ok" 'BEGIN{printf "%.3f",s/n}')"
  avg_p="$(awk -v s="$sum_p" -v n="$ok" 'BEGIN{printf "%.3f",s/n}')"
  score="$(awk -v a="$avg_q" -v b="$avg_p" 'BEGIN{printf "%.3f",a+b}')"

  printf "%s\t%d\t%s\t%s\t%s\t%s\n" "$dns" "$ok" "$avg_q" "$avg_p" "$score" "$sample_ip" >> "$tmp"
done

echo "Domain=$DOMAIN  Rounds=$ROUNDS  PingCount=$PING_COUNT"
printf "resolver\tok\tdns_ms\tping_ms\tscore_ms\tsample_ip\n"

sorted="$(sort -t $'\t' -k5,5n "$tmp")"
if command -v column >/dev/null 2>&1; then
  echo "$sorted" | column -t -s $'\t'
else
  echo "$sorted"
fi

best_line="$(echo "$sorted" | head -n1)"
best_dns="$(awk -F'\t' '{print $1}' <<<"$best_line")"
best_score="$(awk -F'\t' '{print $5}' <<<"$best_line")"

echo
echo "BEST_DNS=$best_dns  (score_ms=$best_score)"

iface="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
if [ -n "$iface" ]; then
  echo "临时切换(重启失效): sudo resolvectl dns $iface $best_dns 1.1.1.1"
fi