#!/usr/bin/env bash
# Enhanced OSD IP mapping - captures ALL OSDs by querying each individually

OUTPUT_FILE="${1:-osd_ip_map.json}"

echo "Building complete OSD IP address mapping..."
echo ""

# Get list of all OSD IDs
OSD_IDS=$(ceph osd ls)

if [ -z "$OSD_IDS" ]; then
    echo "Error: No OSDs found. Check cluster connectivity."
    exit 1
fi

TOTAL_OSDS=$(echo "$OSD_IDS" | wc -w)
echo "Found $TOTAL_OSDS OSDs in cluster: $OSD_IDS"
echo ""

# Create Python script to build the mapping
python3 << 'EOF' > "$OUTPUT_FILE"
import subprocess
import json
import re
import sys

def get_osd_ips(osd_id):
    """Get all IP addresses for a specific OSD."""
    ips = set()

    try:
        # Method 1: ceph osd find
        result = subprocess.run(
            ['ceph', 'osd', 'find', str(osd_id), '--format=json'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)

            # Extract IPs from various address fields
            for key in ['ip', 'public_addr', 'cluster_addr', 'front_addr', 'back_addr']:
                if key in data:
                    addr = data[key]
                    # Extract IP from formats like "10.8.223.224:6800/1234" or "v2:10.8.223.224:6800/1234"
                    matches = re.findall(r'(\d+\.\d+\.\d+\.\d+)', str(addr))
                    ips.update(matches)

            # Also check crush_location for host info
            if 'crush_location' in data and 'host' in data['crush_location']:
                host = data['crush_location']['host']
                sys.stderr.write(f"  osd.{osd_id} -> host {host}\n")

    except Exception as e:
        sys.stderr.write(f"  Warning: Could not get info for osd.{osd_id}: {e}\n")

    # Method 2: ceph osd metadata (fallback)
    if not ips:
        try:
            result = subprocess.run(
                ['ceph', 'osd', 'metadata', str(osd_id), '--format=json'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                metadata = json.loads(result.stdout)
                for key in ['front_addr', 'back_addr', 'public_addr', 'cluster_addr']:
                    if key in metadata:
                        matches = re.findall(r'(\d+\.\d+\.\d+\.\d+)', str(metadata[key]))
                        ips.update(matches)
        except Exception as e:
            sys.stderr.write(f"  Warning: Metadata lookup failed for osd.{osd_id}: {e}\n")

    return list(ips)

# Get all OSD IDs
try:
    result = subprocess.run(['ceph', 'osd', 'ls'], capture_output=True, text=True, timeout=5)
    osd_ids = [int(x) for x in result.stdout.strip().split()]
except Exception as e:
    sys.stderr.write(f"Error getting OSD list: {e}\n")
    sys.exit(1)

sys.stderr.write(f"Querying {len(osd_ids)} OSDs...\n")

# Build the mapping
ip_map = {}
osd_multi_ip = {}  # Track OSDs with multiple IPs

for osd_id in sorted(osd_ids):
    sys.stderr.write(f"  Querying osd.{osd_id}... ")
    ips = get_osd_ips(osd_id)

    if ips:
        sys.stderr.write(f"found {len(ips)} IP(s): {', '.join(ips)}\n")

        # Use first IP as primary, but store all
        primary_ip = ips[0]
        ip_map[primary_ip] = f"osd.{osd_id}"

        # If multiple IPs, map them all to the same OSD
        if len(ips) > 1:
            osd_multi_ip[f"osd.{osd_id}"] = ips
            for ip in ips[1:]:
                ip_map[ip] = f"osd.{osd_id}"
    else:
        sys.stderr.write(f"NO IPs found!\n")

# Print summary to stderr
sys.stderr.write(f"\n")
sys.stderr.write(f"Summary:\n")
sys.stderr.write(f"  Total OSDs: {len(osd_ids)}\n")
sys.stderr.write(f"  OSDs mapped: {len(set(ip_map.values()))}\n")
sys.stderr.write(f"  Unique IPs: {len(ip_map)}\n")

if osd_multi_ip:
    sys.stderr.write(f"\nOSDs with multiple IPs:\n")
    for osd, ips in osd_multi_ip.items():
        sys.stderr.write(f"  {osd}: {', '.join(ips)}\n")

# Output JSON to stdout
print(json.dumps(ip_map, indent=2, sort_keys=True))
EOF

if [ $? -eq 0 ] && [ -s "$OUTPUT_FILE" ]; then
    echo ""
    echo "=========================================="
    echo "✓ OSD mapping saved to $OUTPUT_FILE"
    echo "=========================================="
    echo ""
    cat "$OUTPUT_FILE"
    echo ""

    # Count unique OSDs
    OSD_COUNT=$(grep -o '"osd\.[0-9]*"' "$OUTPUT_FILE" | sort -u | wc -l)
    IP_COUNT=$(grep -c '": "osd\.' "$OUTPUT_FILE")

    echo "Mapped $OSD_COUNT OSDs across $IP_COUNT IP addresses"

    if [ "$OSD_COUNT" -ne "$TOTAL_OSDS" ]; then
        echo ""
        echo "⚠ Warning: Expected $TOTAL_OSDS OSDs but only mapped $OSD_COUNT"
        echo "Some OSDs may not have accessible IP information"
    fi
else
    echo "✗ Failed to build OSD mapping"
    exit 1
fi
