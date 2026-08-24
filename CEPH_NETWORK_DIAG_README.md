# Ceph Network Diagnostics Tool

Comprehensive network health diagnostic script for Ceph clusters.

## Overview

`ceph_network_diag.sh` performs automated diagnostics to identify network problems in Ceph clusters by checking:

- Ceph cluster health and slow requests
- OSD performance metrics (commit/apply latency)
- Network interface errors and drops
- TCP retransmission statistics
- Connectivity between OSD hosts
- OSD-to-OSD network latency (>1000ms threshold)

## Quick Start

```bash
# Auto-detect interface
sudo ./ceph_network_diag.sh

# Specify interface
sudo ./ceph_network_diag.sh bond1

# Specify interface and ceph.conf location
sudo ./ceph_network_diag.sh enp0s3 /etc/ceph/ceph.conf
```

## Output

Creates timestamped directory: `ceph_diag_YYYYMMDD_HHMMSS/`

Contains:
- `diagnostic_report.txt` - Main summary report
- `osd_perf.txt` - OSD performance metrics
- `interface_stats.txt` - Network interface statistics
- `ethtool_stats.txt` - Detailed NIC statistics
- `connections_with_retrans.txt` - TCP connections with retransmits
- `osd_network_dump_*.json` - Per-OSD network dumps

## What It Checks

### 1. Ceph Cluster Health
- Overall cluster status
- Slow requests (network/OSD bottlenecks)
- Flapping OSDs (network instability indicator)

### 2. OSD Performance
- Commit and apply latency per OSD
- Flags OSDs with >100ms latency
- OSD utilization and distribution

### 3. Network Interface Statistics
- RX/TX errors and drops
- Detailed NIC error counters (via ethtool)
- Link speed and duplex mismatches
- Interface status

### 4. TCP Statistics
- Overall retransmission rate
- Active OSD connections
- Specific connections with retransmits

### 5. Connectivity Tests
- Ping tests to all OSD hosts
- Packet loss detection
- High latency identification (>10ms)

### 6. OSD Network Dumps
- Checks `dump_osd_network` on local OSDs
- Identifies connections exceeding 1000ms threshold

### 7. Bandwidth Testing (Optional)
- Instructions for iperf3 testing between hosts

## Requirements

**Required:**
- Bash
- `ip`, `ss`, `netstat` commands
- Access to Ceph cluster (for full diagnostics)

**Optional (for full functionality):**
- Root access (sudo)
- `ethtool` for detailed NIC statistics
- `iperf3` for bandwidth testing
- Ceph admin sockets access

## Understanding the Output

### Issue Severity

- `[OK]` - No problems detected
- `[WARNING]` - Potential issue, investigate
- `[CRITICAL]` - Definite problem requiring attention

### Common Issues Detected

**High Retransmit Rate (>1%)**
- Indicates packet loss or congestion
- Check interface errors with `ethtool -S`

**Interface Errors/Drops**
- Physical layer problem (cable, NIC, switch)
- Check connections and hardware

**Slow OSDs (>100ms latency)**
- Could be network or disk related
- Cross-reference with network stats

**Flapping OSDs**
- Network instability
- Check connectivity and packet loss

**High Ping Latency (>10ms)**
- Between hosts in same data center is unusual
- Investigate routing, switch configuration

## Use Cases

### Scenario 1: Cluster Slow Requests
```bash
# Run diagnostics
sudo ./ceph_network_diag.sh

# Look for:
# - OSDs with high commit/apply latency
# - Interface errors/drops
# - TCP retransmissions
# - Connectivity issues
```

### Scenario 2: Intermittent OSD Flapping
```bash
# Run during normal operation
sudo ./ceph_network_diag.sh bond1

# Check:
# - Packet loss in connectivity tests
# - Interface error counters
# - OSD network dumps for latency spikes
```

### Scenario 3: Performance Degradation
```bash
# Run diagnostics
sudo ./ceph_network_diag.sh

# Then run real-time monitoring
sudo ./live_osd_monitor.sh bond1 6800-7100 5

# Compare:
# - Which OSDs show high latency in both tools
# - Correlate with interface errors
```

## Integration with ceph-rtt-tracker

This diagnostic script provides a **one-time snapshot** of network health.

For **continuous real-time monitoring**, use `ceph-rtt-tracker`:

```bash
# Run diagnostics first
sudo ./ceph_network_diag.sh bond1

# If issues found, start real-time monitoring
sudo ./live_osd_monitor.sh bond1 6800-7100 5
```

**Workflow:**
1. Run `ceph_network_diag.sh` to identify if network issues exist
2. Use `ceph-rtt-tracker` (live_osd_monitor.sh) to pinpoint specific connections
3. Check interface stats in diagnostic output
4. Test bandwidth with iperf3 between problematic hosts

## Troubleshooting

**"ceph command not found"**
- Script will skip Ceph-specific checks
- Still performs network interface diagnostics

**"Not running as root"**
- Some checks limited (ethtool, detailed stats)
- Run with `sudo` for complete diagnostics

**"No OSD hosts to test"**
- Ensure Ceph cluster is accessible
- Check `ceph osd tree` manually

**"No interface detected"**
- Specify interface manually: `./ceph_network_diag.sh bond1`

## Example Output

```
==================================================================================
DIAGNOSTIC SUMMARY
==================================================================================
[CRITICAL] Found 1 critical issue(s)
[WARNING] Found 3 warning(s)

Next steps:
  1. Review detailed logs in ceph_diag_20260525_143022/
  2. For real-time RTT monitoring, use: ./live_osd_monitor.sh
  3. Check interface errors: ethtool -S bond1
  4. Test bandwidth between slow hosts with iperf3
```

## Tips

- Run during normal operation for baseline
- Run during issues for comparison
- Save output for historical analysis
- Run on multiple OSD hosts for complete picture
- Combine with `ceph-rtt-tracker` for deep analysis

## Related Tools

- **ceph-rtt-tracker** (`live_osd_monitor.sh`) - Real-time RTT monitoring
- **Ceph built-in**: `ceph health detail`, `ceph osd perf`
- **Network tools**: `ethtool`, `iperf3`, `tcpdump`
