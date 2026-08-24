# RTT Measurement Accuracy Guide

Understanding and validating the accuracy of OSD ping measurements in ceph-rtt-tracker.

## How ceph-rtt-tracker Measures RTT

### TCP Timestamp Method

The tool uses **TCP timestamp options** (RFC 1323) to calculate round-trip time:

```
Client sends packet:     TSval=1000, TSecr=0
Server replies:          TSval=2000, TSecr=1000  <- echoes client's 1000
Client calculates RTT:   (current_time - time_when_sent_TSval_1000)
```

**Key Points:**
- Passive measurement (no test traffic generated)
- Measures actual production OSD traffic
- Resolution limited by TCP timestamp clock (HZ)
- Captures network + TCP stack latency

## Accuracy Factors

### 1. TCP Timestamp Clock Resolution

**Issue**: Linux TCP timestamps use kernel jiffies, not high-resolution timers.

**Impact:**
- `HZ=100` → 10ms resolution (timestamps increment every 10ms)
- `HZ=250` → 4ms resolution  
- `HZ=1000` → 1ms resolution (most modern kernels)

**Consequence**: RTT measurements are quantized to the HZ interval.

**Check your system:**
```bash
grep "CONFIG_HZ=" /boot/config-$(uname -r)
```

### 2. What RTT Includes

**TCP Timestamp RTT measures:**
- ✓ Network propagation delay (wire time)
- ✓ Switch/router processing
- ✓ Kernel TCP stack processing on both ends
- ✓ Packet queueing delays

**Does NOT include:**
- ✗ Application processing time after TCP ACK
- ✗ OSD messenger layer overhead
- ✗ Disk I/O time

### 3. Measurement Constraints

**Minimum RTT:**
- Theoretical: ~0.01ms (10μs) for same-rack servers
- Typical ICMP ping: 0.1-0.5ms
- TCP timestamp: 1-2ms (limited by HZ)

**Maximum useful RTT:**
- Current filter: 1000ms
- Data center typical: <10ms
- Cross-DC: 10-100ms

### 4. Connection State

RTT varies based on:
- TCP congestion state
- Connection age (slow start vs steady state)
- Queue depth at endpoints
- Competing traffic

## Validation Methods

### Method 1: Compare to ICMP Ping

**Expected:**  
`TCP RTT ≈ ICMP ping + 1-3ms`

```bash
# ICMP baseline
ping -c 100 -i 0.01 ceph502 | tail -1

# Compare to TCP measurements from ceph-rtt-tracker
```

**Why the difference?**
- ICMP: kernel → kernel
- TCP: kernel + connection state + timestamp quantization

### Method 2: Compare to Ceph OSD Ping

**Expected:**  
`Ceph OSD ping > TCP RTT`

```bash
# Ceph's measurement (includes OSD messenger)
ceph tell osd.0 ping osd.1

# Should be higher than TCP RTT in ceph-rtt-tracker
```

**Why the difference?**
- Ceph OSD ping includes application-level processing
- TCP timestamp captures only up to ACK

### Method 3: Packet Capture Analysis

Manually calculate RTT from packet timestamps:

```bash
# Capture traffic
tcpdump -i enp0s3 -w /tmp/test.pcap 'tcp port 6802' -c 1000

# Analyze with tshark
tshark -r /tmp/test.pcap -Y "tcp.options.timestamp.tsval" \
    -T fields -e frame.time -e tcp.options.timestamp.tsval \
    -e tcp.options.timestamp.tsecr
```

Compare calculated RTT to ceph-rtt-tracker output.

## Expected RTT Ranges

### Data Center (same rack)
- ICMP ping: **0.1-0.3ms**
- TCP timestamp: **1-3ms** (limited by HZ)
- Ceph OSD ping: **2-5ms**

### Data Center (different rack/switch)
- ICMP ping: **0.3-1ms**
- TCP timestamp: **1-5ms**
- Ceph OSD ping: **3-10ms**

### Cross Data Center
- ICMP ping: **10-50ms**
- TCP timestamp: **10-50ms**
- Ceph OSD ping: **15-60ms**

## Accuracy Limitations

### Known Issues

1. **Timestamp Quantization**
   - Sub-millisecond RTTs appear as 0-2ms
   - Cannot distinguish 0.1ms from 0.9ms
   - **Impact**: Low for troubleshooting (problems are >10ms)

2. **Timestamp Reuse**
   - Fixed in current implementation (connection-aware matching)
   - Old bug: could match timestamps across connections

3. **Direction Validation**
   - Fixed in current implementation (checks packet direction)
   - Old bug: could match same-direction packets

4. **Out-of-Order Packets**
   - Rare in data center
   - Could cause incorrect RTT if ACK arrives before data
   - **Mitigation**: Min bound filter (0.01ms)

### When to Trust the Measurements

**Trust when:**
- ✓ RTT > 5ms (well above HZ quantization)
- ✓ Consistent over multiple samples
- ✓ Aligns with ICMP ping trend (if X is faster than Y in ping, same in TCP)
- ✓ Sample count > 100

**Be cautious when:**
- ⚠ RTT < 2ms (may be HZ artifact)
- ⚠ Sample count < 10
- ⚠ Huge variance (0.5ms to 500ms)
- ⚠ Contradicts ICMP ping ordering

## Improving Accuracy

### 1. Verify TCP Timestamps Enabled

```bash
# Check current setting
sysctl net.ipv4.tcp_timestamps

# Enable if disabled
sudo sysctl -w net.ipv4.tcp_timestamps=1

# Make persistent
echo "net.ipv4.tcp_timestamps=1" | sudo tee -a /etc/sysctl.conf
```

### 2. Increase Sample Size

Current: 1000 samples per connection (maxlen=1000 in deque)

Larger samples = more statistical confidence
```python
# In live_analyzer.py
self.osd_rtts = defaultdict(lambda: deque(maxlen=5000))  # Increase from 1000
```

### 3. Filter Outliers

Add percentile-based outlier filtering:

```python
# Keep only P5 to P95 (remove extreme outliers)
def filter_outliers(rtts):
    sorted_rtts = sorted(rtts)
    p5 = sorted_rtts[int(len(sorted_rtts)*0.05)]
    p95 = sorted_rtts[int(len(sorted_rtts)*0.95)]
    return [r for r in rtts if p5 <= r <= p95]
```

### 4. Add Median in Addition to Mean

Median is more robust to outliers:

```python
# Already using: mean(rtt_list)
# Add: median(rtt_list)
```

### 5. Track RTT Distribution

Show min/p25/p50/p75/p90/p99/max per connection:

```python
def percentile(data, p):
    sorted_data = sorted(data)
    return sorted_data[int(len(sorted_data) * p)]
```

## Validation Workflow

### Step 1: Run Validation Script

```bash
sudo ./validate_rtt_accuracy.sh enp0s3
```

This compares:
- ICMP ping
- Ceph OSD ping  
- TCP timestamp measurements
- Raw packet analysis

### Step 2: Check Relationships

Expected ordering (same host pair):
```
ICMP < TCP timestamp < Ceph OSD ping
0.2ms < 1.5ms        < 3ms
```

If violated, investigate:
- Check TCP timestamps enabled
- Verify HZ value
- Look for network issues

### Step 3: Statistical Validation

For each connection pair, verify:
1. **Consistency**: Stddev < 20% of mean
2. **Sample size**: >100 measurements
3. **Range**: Max < 10× min (for healthy connections)

### Step 4: Cross-Reference

Pick a slow connection from ceph-rtt-tracker, then:

```bash
# Verify with manual ping
ping -c 100 <slow_host>

# Verify with Ceph
ceph tell osd.X ping osd.Y

# All should agree this pair is slow
```

## Common Pitfalls

### False High RTT

**Symptom**: RTT shows 500ms but network is fine

**Causes:**
- Timestamp wraparound (TSval rolled over)
- ✓ Fixed: Connection-aware matching prevents this
- Wrong direction match
- ✓ Fixed: Direction validation added

**Verify:** Check raw packets with tcpdump

### False Low RTT

**Symptom**: RTT shows 0.01ms (impossible in real network)

**Causes:**
- Same-packet timestamp echo (loopback)
- Kernel bypass/offload
- Measurement bug

**Fix:** Increase minimum bound to 0.1ms

### Inconsistent Results

**Symptom**: Same connection shows 2ms then 200ms

**Causes:**
- Network actually has high variance (investigate!)
- Packet loss causing retransmits
- Competing traffic

**Verify:** Run iperf3 and watch variance

## Tuning the Tool

### Current Settings (live_analyzer.py)

```python
# RTT bounds (line ~137)
if 0.01 < rtt_ms < 1000:

# Sample size per connection (line ~20)
self.osd_rtts = defaultdict(lambda: deque(maxlen=1000))

# Overall sample size (line ~19)
self.rtts = deque(maxlen=10000)

# Minimum samples for reporting (line ~225)
if len(rtt_list) >= 5:
```

### Recommended for Higher Accuracy

```python
# Tighter bounds for data center
if 0.1 < rtt_ms < 500:  # Reject <0.1ms as noise, >500ms as error

# Larger sample size
self.osd_rtts = defaultdict(lambda: deque(maxlen=5000))

# Higher minimum for statistics
if len(rtt_list) >= 50:  # More confidence
```

## References

- RFC 1323: TCP Extensions for High Performance (TCP timestamps)
- Linux kernel: `net/ipv4/tcp_output.c` (timestamp generation)
- Ceph OSD messenger: measures application-level RTT
- `ceph-rtt-tracker`: Passive TCP timestamp monitoring

## Quick Reference

| Method | Resolution | Includes | Use Case |
|--------|-----------|----------|----------|
| ICMP ping | ~0.1ms | Network only | Baseline |
| TCP timestamp | 1-10ms | Network + TCP | Passive monitor |
| Ceph OSD ping | Variable | Network + TCP + OSD | Application RTT |
| Packet capture | μs | Everything | Deep analysis |

**Bottom line**: ceph-rtt-tracker is accurate for **identifying problem connections** (>10ms), but not precise for measuring sub-millisecond differences.
