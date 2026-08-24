#!/usr/bin/env bash
# Build a mapping of IP addresses to OSD IDs

OUTPUT_FILE="${1:-osd_ip_map.json}"

echo "Building OSD IP address mapping..."

# Get all OSD metadata and extract IP addresses
# This creates a JSON mapping: {"10.8.223.224": "osd.0", ...}

ceph osd metadata --format=json | python3 -c "
import json
import sys
import re

data = json.load(sys.stdin)
ip_map = {}

for osd_meta in data:
    osd_id = osd_meta.get('id')

    # Try multiple fields where IP might be stored
    # front_addr format: \"10.8.223.224:6800/1234\"
    # back_addr format: \"10.8.223.224:6801/1234\"
    for addr_field in ['front_addr', 'back_addr', 'public_addr', 'cluster_addr']:
        if addr_field in osd_meta:
            addr = osd_meta[addr_field]
            # Extract IP from \"IP:PORT/NONCE\" or \"v2:IP:PORT/NONCE\" format
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)', addr)
            if match:
                ip = match.group(1)
                ip_map[ip] = f'osd.{osd_id}'
                break

print(json.dumps(ip_map, indent=2))
" > "$OUTPUT_FILE"

if [ $? -eq 0 ] && [ -s "$OUTPUT_FILE" ]; then
    echo "✓ OSD mapping saved to $OUTPUT_FILE"
    echo ""
    echo "Found OSDs:"
    cat "$OUTPUT_FILE"
    echo ""
    echo "Total OSDs: $(cat "$OUTPUT_FILE" | grep -c 'osd\.')"
else
    echo "✗ Failed to build OSD mapping"
    echo "Make sure 'ceph' command is available and you have access to the cluster"
    exit 1
fi
