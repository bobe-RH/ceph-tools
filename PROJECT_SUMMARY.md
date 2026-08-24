# Project Summary

## 1. Ceph RTT Tracker
**Files**: `ceph_network_check.sh`, `live_osd_monitor.sh`, `live_analyzer.py`, `ceph_network_diag.sh`, `validate_rtt_accuracy.sh`, `osd_heartbeat_monitor.sh`, `compare_ping_methods.sh`, `build_osd_map_with_ports.sh`, `osd_ip_map.json`

Live OSD network latency monitoring system. Captures TCP traffic on OSD ports (6800-7100) via tshark and uses TCP timestamp options (RFC 1323) to calculate real-time round-trip times between OSDs. Tracks RTT per individual connection flow with direction validation. `ceph_network_check.sh` is the unified entry point with quick/full/monitor modes. Includes validation against ICMP ping and comparison with Ceph's internal heartbeat data. RHEL 7 compatible.

## 2. Ceph Multi-Cluster Dashboard
**Files**: `ceph_dashboard.py`, `clusters.json`

Curses-based terminal dashboard monitoring 4+ Ceph clusters (ceph301, ceph401, ceph501, ceph601). Batches 7 ceph commands into a single SSH session per cluster for efficiency. Overview shows all clusters at a glance (health, MONs, OSDs, PGs, capacity bars, IO, alerts, clock skew, version). Detail view drills down per cluster with OSD latency tables and pool usage. Python 3 stdlib only.

## 3. RGW Multisite Sync Dashboard
**Files**: `rgw_sync_dashboard.py`, `multisite.json`

Curses-based dashboard for monitoring RGW multisite sync across realm "movies" (us-east on ceph12, us-west on ceph5). Five views: Overview (sync status per zone), Bucket Sync (per-bucket table), Period Validation (side-by-side config comparison), Sync Errors, and Bucket Detail (shard-level). Batches `radosgw-admin` commands via SSH, parses text output of `sync status` with regex since it has no JSON mode. Python 3 stdlib only.

## 4. Bucket2 Index Recovery & Data Resync
**Files**: `init_bucket2_index.py`, `sync_bucket2.py`, `bucket2_instance.json`

Diagnosed and fixed a corrupted bucket index on us-east (ceph12). Root cause: the bucket instance RADOS object had stale `layout_logs` from previous reshards encoding a non-zero generation — RGW looked for generation-prefixed shard names that didn't exist. Fixed by replacing the instance object with the us-west binary (gen 0). Multisite `bucket sync run` couldn't resync data (datalog markers already consumed), so we built `sync_bucket2.py` — a boto3 script with 8 parallel workers that streamed ~57K objects (~1.2 TiB) directly from ceph5 to ceph12 at ~92 MiB/s.
