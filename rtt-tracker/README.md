# Ceph RTT Tracker

Live OSD network latency monitoring using TCP timestamp analysis.

Captures TCP traffic on OSD ports (6800-7100) via tshark and uses TCP
timestamp options (RFC 1323) to calculate real-time round-trip times
between OSDs. Passive monitoring only -- no test traffic is injected.

## Requirements

- **tshark** (from wireshark package): `yum install wireshark`
- **python3** (standard library only, no pip packages)
- **bash** (RHEL 7+ compatible)
- **root/sudo** for packet capture and live monitoring
- **ceph** CLI for OSD mapping (optional but recommended)

## Quick Start

Run the unified entry point with no arguments to get an interactive menu:

```
sudo ./ceph_network_check.sh
```

Or pass an interface and mode directly:

```
sudo ./ceph_network_check.sh eth0 quick
sudo ./ceph_network_check.sh bond1 full
sudo ./ceph_network_check.sh eth0 monitor
```

The interface is auto-detected if omitted.

## Modes

| Mode | Duration | What it does |
|------|----------|--------------|
| `quick` | ~30s | Network diagnostics snapshot (health, errors, drops) |
| `full` | ~2min | Diagnostics + RTT validation + 30s live monitoring sample |
| `monitor` | Continuous | Real-time OSD latency tracking (Ctrl+C to stop) |
| `validate` | ~1min | Compare TCP timestamp RTT against ICMP ping |
| `interactive` | -- | Menu-driven selection (default when no mode given) |

## Scripts

| Script | Purpose |
|--------|---------|
| `ceph_network_check.sh` | Unified entry point with quick/full/monitor/validate modes |
| `live_osd_monitor.sh` | Launches real-time OSD latency monitor (tshark + live_analyzer.py) |
| `live_analyzer.py` | Real-time analysis engine: connection-aware RTT calculation |
| `ceph_network_diag.sh` | Network diagnostics snapshot (interface stats, TCP errors, connectivity) |
| `validate_rtt_accuracy.sh` | Compares TCP timestamp RTT vs ICMP ping vs Ceph OSD ping |
| `osd_heartbeat_monitor.sh` | Extracts Ceph internal OSD heartbeat data via admin socket |
| `compare_ping_methods.sh` | Side-by-side comparison of ICMP, TCP timestamp, and Ceph heartbeat RTT |
| `build_osd_map.sh` | Builds IP-to-OSD mapping from `ceph osd metadata` |
| `build_osd_map_enhanced.sh` | Enhanced mapping: queries each OSD individually for full coverage |
| `build_osd_map_with_ports.sh` | Port-aware mapping: includes IP:PORT entries for exact OSD identification |
| `analyze_rtts.py` | Offline RTT analysis from previously captured tshark CSV data |
| `parse_osd_pings.py` | Parses OSD ping data from tcpdump hex output |
| `extract_osd_rtts.sh` | Extracts TCP timestamp fields from a pcap file into CSV |
| `debug_live_monitor.sh` | Step-by-step diagnostic for troubleshooting the live monitor |

## OSD Mapping

The live monitor resolves raw IP:PORT pairs to OSD identifiers using a
JSON mapping file (`osd_ip_map.json`). Three scripts can build this file:

1. **build_osd_map.sh** -- Basic mapping from `ceph osd metadata`. Maps
   each IP to a single OSD. Fast but may miss OSDs sharing an IP.

2. **build_osd_map_enhanced.sh** -- Queries each OSD individually via
   `ceph osd find`. Handles multiple IPs per OSD and multiple OSDs per host.

3. **build_osd_map_with_ports.sh** -- Best accuracy. Includes IP:PORT
   entries so the monitor can match traffic to exact OSDs rather than
   guessing when several OSDs share one IP.

The live monitor tries these in order (ports > enhanced > basic) and
falls back to displaying raw IP addresses if none succeed. To build the
map manually:

```
sudo ./build_osd_map_with_ports.sh osd_ip_map.json
```

The resulting JSON looks like:

```json
{
  "10.0.0.1:6800": "osd.0",
  "10.0.0.1:6801": "osd.1",
  "10.0.0.2:6800": "osd.2"
}
```

## Understanding Output

The live monitor displays a refreshing dashboard (default every 5 seconds)
with three sections:

**Overall Latency Statistics** -- min, mean, median, P95, P99, and max
RTT across the last 10,000 measurements.

**Delay Distribution** -- counts and percentages in five severity buckets:

| Bucket | Range | Severity |
|--------|-------|----------|
| Fast | < 1 ms | Normal for same-rack or same-switch OSDs |
| Normal | 1 -- 10 ms | Typical cross-rack or cross-switch |
| Slow | 10 -- 100 ms | Worth investigating |
| Very Slow | 100 -- 500 ms | Likely impacting performance |
| Critical | >= 500 ms | Immediate attention needed |

In a healthy data center cluster, 80%+ of measurements should fall in the
Fast bucket.

**Top 10 Connections** -- the highest-latency OSD-to-OSD flows, sorted
by average RTT. Endpoint labels use this notation:

- `host1:osd.0` -- exact port match
- `host1:osd.0~6805` -- adjacent port match (approximate)
- `host1[0,1]:6802` -- ambiguous: multiple OSDs share the IP
- `10.0.0.1:6800` -- no OSD mapping available

## RTT Accuracy

**Method**: TCP timestamp options (TSval/TSecr echo per RFC 1323).
Each packet carries a timestamp; the peer echoes it back, and the time
delta gives the round-trip time at the TCP layer.

**What it measures**: network propagation + TCP stack processing.
It does NOT include application-layer (OSD messenger) processing.

**Resolution**: limited by the kernel timer frequency (CONFIG_HZ).
With HZ=1000 (common on RHEL 7+), resolution is approximately 1 ms.

**Expected relationship between methods**:

```
ICMP ping  <  TCP timestamp RTT  <  Ceph OSD heartbeat RTT
(network)     (network + stack)     (network + stack + app)
```

**Validation workflow**: run `validate` mode to automatically compare all
three methods. It captures ICMP pings, TCP timestamp RTTs, and Ceph OSD
ping results, then prints them side by side:

```
sudo ./ceph_network_check.sh eth0 validate
```

Results are saved to a timestamped directory for later review.

## Troubleshooting

**"tshark not found"**
Install the wireshark package:
```
yum install wireshark
```

**"Not running as root"**
Packet capture requires root. Run with sudo:
```
sudo ./ceph_network_check.sh
```

**No data / "Waiting for packets"**
- Confirm the correct network interface: `ip addr show`
- Verify OSD traffic exists on ports 6800-7100: `ss -tnp | grep 680`
- Check that TCP timestamps are enabled: `cat /proc/sys/net/ipv4/tcp_timestamps` (should be 1)
- Run the debug helper: `sudo ./debug_live_monitor.sh eth0`

**OSD names show as raw IPs**
Build the OSD mapping file first:
```
sudo ./build_osd_map_with_ports.sh osd_ip_map.json
```

**High RTT outliers (100-999 ms) in an otherwise healthy cluster**
Some outliers are expected. TCP delayed ACKs and retransmissions can
produce measured RTTs well above the true network latency. Look at the
median and P95 rather than individual outliers.

**"Could not build OSD mapping"**
The `ceph` CLI must be available and configured (`/etc/ceph/ceph.conf`).
Verify with:
```
ceph osd ls
```

**Wrong interface auto-detected**
Pass the interface explicitly:
```
sudo ./ceph_network_check.sh bond1 monitor
```
