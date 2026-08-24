#!/usr/bin/env bash
# Build OSD mapping with IP:PORT information

OUTPUT_FILE="${1:-osd_ip_map.json}"

echo "Building OSD IP:PORT mapping..."
echo ""

# Use ceph osd find to get IP and port for each OSD
python3 << 'EOF' > "$OUTPUT_FILE"
import subprocess
import json
import re
import sys

def get_osd_addresses(osd_id):
    """Get IP:PORT addresses for a specific OSD."""
    addresses = {}

    try:
        result = subprocess.run(
            ['ceph', 'osd', 'find', str(osd_id), '--format=json'],
            capture_output=True,
            text=True,
            timeout=15
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)

            # Get host info
            host = data.get('host', 'unknown')

            # Extract addresses from addrs.addrvec array
            if 'addrs' in data and 'addrvec' in data['addrs']:
                addr_list = []
                for addr_entry in data['addrs']['addrvec']:
                    if 'addr' in addr_entry:
                        # Format: "10.8.223.200:6802"
                        addr = addr_entry['addr']
                        addr_type = addr_entry.get('type', 'unknown')

                        # Extract IP and port
                        match = re.match(r'(\d+\.\d+\.\d+\.\d+):(\d+)', addr)
                        if match:
                            ip = match.group(1)
                            port = match.group(2)

                            # Store IP:PORT mapping
                            ip_port_key = f'{ip}:{port}'
                            addresses[ip_port_key] = f'osd.{osd_id}'
                            addr_list.append(f'{ip_port_key}({addr_type})')

                            # Also store just IP as fallback
                            if ip not in addresses:
                                addresses[ip] = f'osd.{osd_id}'

                sys.stderr.write(f"  osd.{osd_id} @ {host}: {', '.join(addr_list)}\n")
            else:
                sys.stderr.write(f"  osd.{osd_id} @ {host}: No addresses found in output\n")

    except Exception as e:
        sys.stderr.write(f"  Error querying osd.{osd_id}: {e}\n")

    return addresses

# Get all OSD IDs
try:
    result = subprocess.run(['ceph', 'osd', 'ls'], capture_output=True, text=True, timeout=30)
    osd_ids = [int(x) for x in result.stdout.strip().split()]
except Exception as e:
    sys.stderr.write(f"Error getting OSD list: {e}\n")
    sys.exit(1)

sys.stderr.write(f"Querying {len(osd_ids)} OSDs...\n\n")

# Build the mapping
ip_map = {}

for osd_id in sorted(osd_ids):
    addresses = get_osd_addresses(osd_id)
    ip_map.update(addresses)

# Count unique mappings
ip_only = sum(1 for k in ip_map.keys() if ':' not in k)
ip_port = sum(1 for k in ip_map.keys() if ':' in k)
unique_osds = len(set(ip_map.values()))

sys.stderr.write(f"\nSummary:\n")
sys.stderr.write(f"  Total OSDs: {len(osd_ids)}\n")
sys.stderr.write(f"  Unique OSDs mapped: {unique_osds}\n")
sys.stderr.write(f"  IP:PORT mappings: {ip_port}\n")
sys.stderr.write(f"  IP-only mappings: {ip_only}\n")
sys.stderr.write(f"  Total entries: {len(ip_map)}\n")

# Output JSON to stdout
print(json.dumps(ip_map, indent=2, sort_keys=True))
EOF

if [ $? -eq 0 ] && [ -s "$OUTPUT_FILE" ]; then
    echo ""
    echo "=========================================="
    echo "✓ OSD mapping saved to $OUTPUT_FILE"
    echo "=========================================="
    echo ""

    # Show sample
    echo "Sample mappings:"
    head -30 "$OUTPUT_FILE"
    echo "  ..."
    echo ""

    UNIQUE_OSDS=$(grep -o '"osd\.[0-9]*"' "$OUTPUT_FILE" | sort -u | wc -l)
    TOTAL_ENTRIES=$(grep -c '": "osd\.' "$OUTPUT_FILE")
    echo "Mapped $UNIQUE_OSDS unique OSDs across $TOTAL_ENTRIES entries"
else
    echo "✗ Failed to build OSD mapping"
    exit 1
fi
