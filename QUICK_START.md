# Ceph Network Tools - Quick Start Guide

One unified command to run all network diagnostics and monitoring.

## The Simple Way - Unified Tool

### Interactive Mode (Recommended)
```bash
sudo ./ceph_network_check.sh
```

Shows a menu - pick what you want to do:
1. Quick Diagnostics (30 seconds)
2. Full Analysis (2 minutes) 
3. Live Monitoring (continuous)
4. RTT Validation
5. Exit

### One-Command Modes

```bash
# Quick health check
sudo ./ceph_network_check.sh enp0s3 quick

# Complete analysis (diagnostics + validation + monitoring sample)
sudo ./ceph_network_check.sh enp0s3 full

# Just live monitoring
sudo ./ceph_network_check.sh enp0s3 monitor

# Just validation
sudo ./ceph_network_check.sh enp0s3 validate
```

**Auto-detect interface:**
```bash
sudo ./ceph_network_check.sh     # Uses default interface
```

## What Each Mode Does

### Quick Mode (~30 seconds)
- Ceph cluster health check
- OSD performance metrics
- Network interface statistics
- TCP connection stats
- Connectivity tests
- OSD network dumps

**Use when:** You want a fast snapshot of network health

### Full Mode (~2 minutes)
Everything in Quick mode, plus:
- RTT accuracy validation (ICMP vs TCP timestamp)
- 30-second live monitoring sample
- Cross-method comparison

**Use when:** Investigating an issue or establishing a baseline

### Monitor Mode (continuous)
- Real-time OSD-to-OSD latency tracking
- Updates every 5 seconds
- Top 10 worst connections
- Press Ctrl+C to stop

**Use when:** Watching cluster during load, testing changes, or troubleshooting

### Validate Mode
- Compare ICMP ping vs TCP timestamp RTT
- Check TCP timestamp configuration
- Verify measurement accuracy

**Use when:** Questioning if your measurements are accurate

## Output

All modes save results to timestamped directories:
```
./ceph_network_check_YYYYMMDD_HHMMSS/
├── diagnostics.log          # Network diagnostic results
├── validation.log           # RTT validation results (full/validate mode)
├── monitoring_sample.log    # Live monitoring sample (full mode)
└── [other supporting files]
```

## Common Workflows

### First Time Setup / Baseline
```bash
# Get complete baseline
sudo ./ceph_network_check.sh enp0s3 full

# Review the output
cd ceph_network_check_*/
less diagnostics.log
less monitoring_sample.log
```

### Daily Health Check
```bash
# Quick daily check
sudo ./ceph_network_check.sh enp0s3 quick

# Review summary
grep -E "CRITICAL|WARNING" ceph_network_check_*/diagnostics.log
```

### Troubleshooting Slow Requests
```bash
# Run full analysis to identify issues
sudo ./ceph_network_check.sh enp0s3 full

# Then start live monitoring to watch in real-time
sudo ./ceph_network_check.sh enp0s3 monitor
```

### Testing a Network Change
```bash
# Before: capture baseline
sudo ./ceph_network_check.sh bond1 full
mv ceph_network_check_* baseline_before/

# Make your change (e.g., adjust MTU, QoS, etc.)

# After: capture new state
sudo ./ceph_network_check.sh bond1 full
mv ceph_network_check_* baseline_after/

# Compare
diff baseline_before/diagnostics.log baseline_after/diagnostics.log
```

### Validating Tool Accuracy
```bash
# Check if TCP timestamp measurements are accurate
sudo ./ceph_network_check.sh enp0s3 validate

# Should see:
# - ICMP ping ~0.2-0.5ms
# - TCP timestamp ~1-5ms (slightly higher, expected)
# - Both should agree on which hosts are slower
```

## Individual Tools (Advanced)

If you need more control, you can still run the individual tools:

### Network Diagnostics
```bash
sudo ./ceph_network_diag.sh enp0s3
```

### Live Monitoring (with custom update interval)
```bash
sudo ./live_osd_monitor.sh enp0s3 6800-7100 10  # Update every 10 seconds
```

### RTT Validation
```bash
sudo ./validate_rtt_accuracy.sh enp0s3
```

## Understanding the Output

### Diagnostics Report
- `[OK]` - No issues
- `[WARNING]` - Potential problem, investigate
- `[CRITICAL]` - Definite issue, needs attention

### Live Monitoring
```
Delay Distribution:
  Fast (<1ms):        81.7% ✓   ← Good
  Normal (1-10ms):     1.9%     ← Acceptable
  Slow (10-100ms):    12.9% ⚠   ← Investigate
  Very Slow (100-500ms): 2.1% ⚠⚠ ← Problem
  Critical (>=500ms):   1.3% 🔴  ← Critical issue
```

**Healthy cluster:**
- 95%+ should be "Fast" or "Normal"
- <5% "Slow"
- 0% "Very Slow" or "Critical"

**Top 10 Connections:**
- Shows worst OSD pairs by average latency
- Focus on connections with:
  - High average (>10ms in data center)
  - High max (>100ms)
  - Large sample size (consistent problem)

### Validation Results

Expected relationship:
```
ICMP ping < TCP timestamp < Ceph OSD ping
0.2-0.5ms < 1-5ms        < 3-10ms
```

If TCP timestamp is much higher than ICMP (>10x), investigate:
- Network congestion
- TCP retransmissions
- Delayed ACKs

## Tips

- **Always use sudo** - packet capture requires root
- **Run from the test directory** - scripts expect to find each other
- **Save output directories** - useful for trending and comparison
- **Check multiple times** - network is dynamic, one sample may not be representative
- **Use monitor mode during load tests** - see real-time impact

## Troubleshooting

**"tshark not found"**
```bash
yum install wireshark
```

**"Permission denied" without sudo**
```bash
sudo ./ceph_network_check.sh
```

**"Scripts not found"**
```bash
cd /home/roemerso/Desktop/test
ls -la *.sh  # Verify all scripts are present
```

**"No interface detected"**
```bash
ip link show  # Find your interface name
sudo ./ceph_network_check.sh <interface_name>
```

## Quick Reference Card

| Command | Time | What It Does | When To Use |
|---------|------|--------------|-------------|
| `./ceph_network_check.sh` | - | Interactive menu | Starting point |
| `... quick` | 30s | Health snapshot | Daily checks |
| `... full` | 2m | Complete analysis | Investigations |
| `... monitor` | ∞ | Real-time tracking | During load/changes |
| `... validate` | 1m | Accuracy check | Verify measurements |

---

## Project: ceph-rtt-tracker

All these tools are part of the **ceph-rtt-tracker** project - a comprehensive suite for monitoring and diagnosing Ceph cluster network latency.

**Core Innovation**: Passive RTT monitoring using TCP timestamps - no test traffic needed!
