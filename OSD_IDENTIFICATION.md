# OSD Identification in Live Monitor

The live monitor now identifies OSDs by their daemon ID (osd.0, osd.1, etc.) instead of just IP addresses.

## Quick Start

The live monitor will automatically build the OSD mapping when you run it:

```bash
sudo ./live_osd_monitor.sh enp0s3 6800-7100
```

## Manual OSD Mapping

If you want to build/refresh the OSD mapping manually:

```bash
# Build the mapping (requires access to ceph commands)
./build_osd_map.sh

# View the mapping
cat osd_ip_map.json
```

Example output:
```json
{
  "10.8.223.224": "osd.0",
  "10.8.223.225": "osd.1",
  "10.8.223.200": "osd.2"
}
```

## Display Changes

### Before (IP addresses):
```
Top 10 OSD Pairs by Average Latency:
--------------------------------------------------------------------------------
OSD 1             OSD 2             Samples  Avg (ms)   Max (ms)
--------------------------------------------------------------------------------
10.8.223.200      10.8.223.224      1000     480.046    5900.301   🔴
10.8.223.224      10.8.223.225      1000     405.499    7958.798   🔴
```

### After (OSD IDs):
```
Top 10 OSD Pairs by Average Latency:
--------------------------------------------------------------------------------
OSD 1             OSD 2             Samples  Avg (ms)   Max (ms)
--------------------------------------------------------------------------------
osd.2             osd.0             1000     480.046    5900.301   🔴
osd.0             osd.1             1000     405.499    7958.798   🔴
```

## How It Works

1. **build_osd_map.sh** queries `ceph osd metadata` to get IP addresses for all OSDs
2. Creates a JSON mapping file: `osd_ip_map.json`
3. **live_analyzer.py** loads this mapping at startup
4. Converts IP addresses to OSD IDs in the display

## Troubleshooting

**"OSD map file not found" warning:**
- The monitor will still work, just showing IP addresses
- Run `./build_osd_map.sh` manually if ceph commands aren't available from the monitoring host

**"Could not build OSD mapping" error:**
- Check that `ceph` command is available: `which ceph`
- Verify cluster access: `ceph health`
- You may need to run from a node with ceph client configured

**Some IPs still show instead of OSD IDs:**
- The IP might not be in the cluster (external traffic, monitors, etc.)
- Rebuild the mapping: `./build_osd_map.sh`

## Updating the Mapping

If you add/remove OSDs or change their IPs:

```bash
# Refresh the mapping
./build_osd_map.sh

# No need to restart the monitor - it loads the file at startup
```

## Using with Offline Analysis

The OSD mapping also works with the offline analyzer:

```bash
# Build the mapping
./build_osd_map.sh

# Modify analyze_rtts.py to load and use the mapping (similar to live_analyzer.py)
```
