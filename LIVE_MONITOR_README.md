# Live OSD Latency Monitor

Real-time monitoring tool for analyzing OSD ping times during live packet capture.

## Quick Start

```bash
# Basic usage (default interface: enp0s3, ports: 6800-7100, update every 5s)
sudo ./live_osd_monitor.sh

# Custom interface
sudo ./live_osd_monitor.sh eth0

# Custom port range and update interval
sudo ./live_osd_monitor.sh enp0s3 6800-7300 3
```

## What It Does

1. **Captures live traffic** on OSD ports using tshark
2. **Extracts TCP timestamps** in real-time
3. **Calculates RTT** by matching request/response pairs
4. **Updates display every N seconds** with:
   - Overall latency statistics (min/mean/median/P95/P99/max)
   - Delay distribution (Fast/Normal/Slow/Critical)
   - Top 10 slowest OSD pairs
   - Running totals

## Features

- ✓ **Zero disk I/O** - everything processed in memory
- ✓ **Rolling window** - keeps last 10,000 RTT measurements
- ✓ **Auto-updating display** - refreshes every 5 seconds (configurable)
- ✓ **Color-coded alerts** - 🔴 for critical delays
- ✓ **Ctrl+C safe** - shows final statistics on exit

## Display Sections

### Overall Statistics
Shows min, mean, median, P95, P99, and max latency across all recent measurements.

### Delay Distribution
Categorizes all RTTs:
- **Fast (<1ms)**: Healthy local network communication
- **Normal (1-10ms)**: Acceptable
- **Slow (10-100ms)**: Minor delays ⚠
- **Very Slow (100-500ms)**: Significant delays ⚠⚠
- **Critical (≥500ms)**: CRITICAL - investigate immediately 🔴

### Top Problem OSDs
Lists the 10 OSD pairs with highest average latency, sorted worst-first.

## Requirements

- `tshark` (Wireshark CLI)
- `python3`
- Root/sudo access (for packet capture)

## Example Output

```
================================================================================
Live OSD Latency Monitor - 2026-05-14 15:30:45
================================================================================
Total Packets: 12,543  |  RTT Measurements: 8,234

Overall Latency Statistics (last 10,000 measurements):
--------------------------------------------------------------------------------
  Min:        0.001 ms
  Mean:      21.452 ms
  Median:     0.105 ms
  P95:       45.321 ms
  P99:      498.765 ms
  Max:      999.123 ms

Delay Distribution:
--------------------------------------------------------------------------------
  Fast (<1ms):              6,891 ( 83.7%) ✓
  Normal (1-10ms):            412 (  5.0%)
  Slow (10-100ms):            567 (  6.9%) ⚠
  Very Slow (100-500ms):      112 (  1.4%) ⚠⚠
  Critical (>=500ms):         252 (  3.1%) 🔴

Top 10 OSD Pairs by Average Latency:
--------------------------------------------------------------------------------
OSD 1             OSD 2             Samples  Avg (ms)   Max (ms)
--------------------------------------------------------------------------------
10.225.9.38       10.225.9.57       145      72.345     998.234     🔴
10.225.10.29      10.225.10.57      234      43.123     987.456     ⚠
10.225.9.57       10.225.9.83       189      38.901     956.789     ⚠
...
```

## Stopping the Monitor

Press **Ctrl+C** to stop. It will display final statistics before exiting.

## Troubleshooting

**No packets captured:**
- Check interface name: `ip link show`
- Verify port range matches your Ceph configuration
- Ensure OSD traffic is active

**Permission denied:**
- Must run with `sudo` for packet capture

**High memory usage:**
- Reduce rolling window size in `live_analyzer.py` (line with `maxlen=10000`)
- Increase update interval to reduce processing frequency

## Files

- `live_osd_monitor.sh` - Main launcher script
- `live_analyzer.py` - Real-time analysis engine
- Uses named pipe (FIFO) for communication

## Comparison to Offline Analysis

| Feature | Live Monitor | Offline (analyze_rtts.py) |
|---------|--------------|---------------------------|
| Disk usage | None | Requires saving pcap |
| Memory | ~50MB | Unlimited (processes all) |
| History | Last 10K RTTs | Full capture |
| Latency | Real-time | Post-capture |
| Use case | Active monitoring | Forensic analysis |
