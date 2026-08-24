# Ceph Tools

Monitoring and management tools for Ceph storage clusters. Python 3.6+ compatible, stdlib only (unless noted).

## Projects

| Project | Description |
|---------|-------------|
| [rtt-tracker](rtt-tracker/) | Live OSD network latency monitoring using TCP timestamps (RFC 1323). Passive RTT measurement via tshark — no test traffic needed. |
| [dashboard](dashboard/) | Curses-based terminal dashboard for monitoring multiple Ceph clusters at a glance. Batches commands over SSH for efficiency. |
| [rgw-sync](rgw-sync/) | RGW multisite sync dashboard. Monitors replication status, bucket sync, datalog shards, and period config across zones. |
| [bucket-recovery](bucket-recovery/) | Bucket index recovery and cross-site data resync. Fixes corrupted bucket instances and parallel-copies objects between RGW endpoints. |
| [rgw-bucket-list](rgw-bucket-list/) | Lists all RGW buckets with owner, object count, and size via the Admin REST API. Pure stdlib SigV4 signing. |
| [audit](audit/) | Ceph audit history. Gathers admin command history and config changes from journalctl, audit log files, and `ceph config log`. |

## Requirements

- Python 3.6+
- SSH access (as root) to Ceph cluster nodes
- Bash (for shell-based tools)
- tshark (RTT tracker only)
- boto3 (bucket-recovery sync script only)

## Usage

Each project directory contains its own README with setup and usage instructions. Most tools run directly with no installation:

```
python3 dashboard/ceph_dashboard.py
python3 rgw-sync/rgw_sync_dashboard.py
python3 audit/ceph_audit.py <mon_host>
python3 rgw-bucket-list/rgw_bucket_list.py
sudo rtt-tracker/ceph_network_check.sh
```
