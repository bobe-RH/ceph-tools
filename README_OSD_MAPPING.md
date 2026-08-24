# OSD Mapping Scripts - Usage Guide

Two scripts are available for building the IP → OSD ID mapping:

## **build_osd_map_enhanced.sh** ⭐ RECOMMENDED

**What it does:**
- Queries EACH OSD individually using `ceph osd find <id>`
- Captures ALL OSDs regardless of network configuration
- Handles multiple IPs per OSD (public/cluster networks)
- More robust and complete

**Usage:**
```bash
./build_osd_map_enhanced.sh
```

**Output:**
```
Building complete OSD IP address mapping...

Found 9 OSDs in cluster: 0 1 2 3 4 5 6 7 8

Querying 9 OSDs...
  Querying osd.0... found 1 IP(s): 10.8.223.200
  Querying osd.1... found 1 IP(s): 10.8.223.225
  ...
  
✓ OSD mapping saved to osd_ip_map.json

Mapped 9 OSDs across 9 IP addresses
```

---

## **build_osd_map.sh** (Original)

**What it does:**
- Queries `ceph osd metadata` in bulk
- Faster but may miss some OSDs
- Good for simple single-network setups

**Usage:**
```bash
./build_osd_map.sh
```

**Limitation:**
- May only capture OSDs with certain address formats
- In your case, only found 3/9 OSDs (the HDD OSDs)

---

## **Which Should You Use?**

### Use **build_osd_map_enhanced.sh** if:
- ✓ You have multiple networks (public/cluster)
- ✓ You have mixed OSD types (HDD/SSD)
- ✓ The original script didn't find all OSDs
- ✓ **Your situation** - only 3 of 9 OSDs were found

### Use **build_osd_map.sh** if:
- Simple single-network cluster
- Need quick results
- Original script worked fine

---

## **Testing the Enhanced Script**

Run it now to capture all 9 OSDs:

```bash
cd /home/roemerso/Desktop/test
./build_osd_map_enhanced.sh
```

Expected result:
```json
{
  "10.8.223.200": "osd.6",
  "10.8.223.224": "osd.8",
  "10.8.223.225": "osd.7",
  "10.X.X.X": "osd.0",
  "10.X.X.X": "osd.1",
  "10.X.X.X": "osd.2",
  "10.X.X.X": "osd.3",
  "10.X.X.X": "osd.4",
  "10.X.X.X": "osd.5"
}
```

All 9 OSDs should be captured.

---

## **Automatic Detection**

The **live_osd_monitor.sh** will automatically:
1. Check for existing `osd_ip_map.json`
2. If not found, try to build it using:
   - `build_osd_map_enhanced.sh` (preferred)
   - Falls back to `build_osd_map.sh` if enhanced not available
3. Start monitoring with OSD IDs

You don't need to run the mapping script manually - just run:
```bash
sudo ./live_osd_monitor.sh enp0s3 6800-7100
```

---

## **Troubleshooting**

**"Some OSDs may not have accessible IP information"**
- OSD might be down
- Network unreachable
- Permission issue with ceph commands

**"Expected 9 OSDs but only mapped 7"**
- Check: `ceph osd tree` - are all OSDs up?
- Check: `ceph osd find <missing-id>` manually
- Some OSDs might be on isolated networks

**Multiple IPs per OSD**
- Normal for separate public/cluster networks
- The script maps ALL IPs to the same OSD ID
- First IP found is used as "primary"
