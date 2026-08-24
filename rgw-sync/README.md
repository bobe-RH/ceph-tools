# RGW Multisite Sync Dashboard

Curses-based terminal dashboard for monitoring RGW multisite replication across zones. Connects to RGW hosts over SSH, batches `radosgw-admin` commands per zone, and parses text output with regex to present sync status in five views.

## Features

- **Overview** -- high-level sync status across all zones, showing replication lag and health.
- **Bucket Sync** -- per-bucket sync progress between zones with behind/caught-up indicators.
- **Period Validation** -- verifies that all zones share the same period and epoch.
- **Sync Errors** -- lists active sync errors with timestamps and affected buckets.
- **Bucket Detail** -- shard-level sync status for a selected bucket, showing per-shard markers and lag.

Batches `radosgw-admin` commands into SSH sessions and parses their text output with regex -- no JSON mode required.

Python 3.6+ stdlib only -- no external dependencies.

## Requirements

- Python 3.6+
- SSH access as root to each RGW host

## Configuration

Create a `multisite.json` file (see `multisite.json.example` for the format):

```json
{
    "realm": "my-realm",
    "zonegroup": "us",
    "zones": [
        {
            "name": "us-east",
            "ssh_host": "rgw-east.example.com",
            "is_master": true
        },
        {
            "name": "us-west",
            "ssh_host": "rgw-west.example.com",
            "is_master": false
        }
    ],
    "defaults": {
        "ssh_user": "root",
        "ssh_port": 22,
        "ssh_key": null
    }
}
```

Top-level fields:

- `realm` -- the RGW realm name.
- `zonegroup` -- the zonegroup name.

Each entry in `zones` requires:

- `name` -- zone name as configured in RGW.
- `ssh_host` -- hostname or IP of an RGW host in that zone.
- `is_master` -- `true` for the master zone, `false` for secondary zones.

The `defaults` block sets values inherited by all zones unless overridden per-zone:

- `ssh_user` -- SSH username (default: `root`).
- `ssh_port` -- SSH port (default: `22`).
- `ssh_key` -- path to a local SSH private key, or `null` to use the default.

## Usage

```
python3 rgw_sync_dashboard.py
python3 rgw_sync_dashboard.py --config multisite.json
```
