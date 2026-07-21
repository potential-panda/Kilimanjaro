#!/usr/bin/env bash
# Run all 18 combos from flip_scan_param_grid.yaml.
# Each combo produces one scan-reports/{id}.json file.
# Re-running is idempotent (overwrites existing output).
set -euo pipefail
cd "$(dirname "$0")/.."

START=2025-07-01
END=2026-06-29
DD_WINDOW=21600
DD_THRESH=1000
OUT=scan-reports

run() {
  local id=$1 fsym=$2 tsym=$3 exec_sz=$4 box=$5 step=$6 schedule=$7
  local out_file="${OUT}/${id}.json"
  echo "=== ${id} ==="
  python tools/flip_scan.py scan \
    --flip-exchange BINANCE --flip-symbol   "$fsym" \
    --target-exchange GMO   --target-symbol "$tsym" \
    --execution-size "$exec_sz" \
    --box-size-rate "$box" --sar-step "$step" \
    --schedule "$schedule" \
    --rolling-dd-window-seconds "$DD_WINDOW" \
    --rolling-dd-threshold-bps  "$DD_THRESH" \
    --start "$START" --end "$END" \
    --out "$out_file"
}

# ── XRP_JPY ──────────────────────────────────────────────────────────────
run xrp-nofilter-1   XRP_USDT XRP_JPY 150    0.003  0.05 nofilter
run xrp-nofilter-2   XRP_USDT XRP_JPY 150    0.0025 0.02 nofilter
run xrp-active-1     XRP_USDT XRP_JPY 150    0.0025 0.04 xrp_active
run xrp-active-2     XRP_USDT XRP_JPY 150    0.0025 0.05 xrp_active
run xrp-inactive-1   XRP_USDT XRP_JPY 150    0.004  0.05 xrp_inactive
run xrp-inactive-2   XRP_USDT XRP_JPY 150    0.004  0.04 xrp_inactive

# ── ETH_JPY ──────────────────────────────────────────────────────────────
run eth-nofilter-1   ETH_USDT ETH_JPY 0.1    0.0025 0.02 nofilter
run eth-nofilter-2   ETH_USDT ETH_JPY 0.1    0.003  0.04 nofilter
run eth-active-1     ETH_USDT ETH_JPY 0.1    0.003  0.02 default_active
run eth-active-2     ETH_USDT ETH_JPY 0.1    0.004  0.02 default_active
run eth-inactive-1   ETH_USDT ETH_JPY 0.1    0.004  0.04 default_inactive
run eth-inactive-2   ETH_USDT ETH_JPY 0.1    0.004  0.05 default_inactive

# ── BTC_JPY ──────────────────────────────────────────────────────────────
run btc-nofilter-1   BTC_USDT BTC_JPY 0.002  0.0025 0.05 nofilter
run btc-nofilter-2   BTC_USDT BTC_JPY 0.002  0.0025 0.02 nofilter
run btc-active-1     BTC_USDT BTC_JPY 0.002  0.0025 0.02 default_active
run btc-active-2     BTC_USDT BTC_JPY 0.002  0.0025 0.05 default_active
run btc-inactive-1   BTC_USDT BTC_JPY 0.002  0.003  0.04 default_inactive
run btc-inactive-2   BTC_USDT BTC_JPY 0.002  0.003  0.05 default_inactive

echo "=== ALL 18 COMBOS DONE ==="
