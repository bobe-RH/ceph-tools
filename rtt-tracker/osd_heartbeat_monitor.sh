#!/usr/bin/env bash
# OSD Heartbeat Monitor - Extract and display Ceph's internal OSD ping data

set -u

# Configuration
UPDATE_INTERVAL="${1:-5}"
MODE="${2:-once}"

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
    cat <<EOF
Usage: $0 [interval] [mode]

Extract and monitor Ceph's internal OSD heartbeat ping data.

Arguments:
    interval    Update interval in seconds (default: 5)
    mode        Operation mode:
                  once       - Single snapshot (default)
                  monitor    - Continuous monitoring
                  export     - Export to JSON/CSV

Examples:
    $0                  # Single snapshot
    $0 5 monitor        # Update every 5 seconds
    $0 0 export         # Export all data to files

This script queries the OSD admin sockets to get internal ping measurements.
EOF
    exit 0
}

if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    usage
fi

# Find all OSD admin sockets
get_osd_sockets() {
    find /var/run/ceph -name "ceph-osd.*.asok" 2>/dev/null | sort
}

# Extract OSD ID from socket path
get_osd_id() {
    local socket="$1"
    basename "$socket" | sed 's/ceph-osd\.\(.*\)\.asok/\1/'
}

# Query single OSD for network data
query_osd_network() {
    local socket="$1"
    local osd_id=$(get_osd_id "$socket")

    if [[ ! -S "$socket" ]]; then
        echo "Error: Socket $socket not accessible" >&2
        return 1
    fi

    ceph daemon "$socket" dump_osd_network 2>/dev/null
}

# Parse and display network data
display_osd_network_data() {
    local socket="$1"
    local osd_id=$(get_osd_id "$socket")

    echo -e "${CYAN}OSD.$osd_id Network Performance:${NC}"
    echo "----------------------------------------"

    local data=$(query_osd_network "$socket")

    if [[ -z "$data" ]]; then
        echo "  No data available"
        return
    fi

    # Parse JSON output
    local threshold=$(echo "$data" | grep -o '"threshold":[0-9]*' | cut -d: -f2)
    local entries=$(echo "$data" | grep -o '"entries":\[.*\]' | sed 's/"entries"://')

    echo "  Threshold: ${threshold}ms (connections slower than this are reported)"
    echo ""

    # Check if there are any entries
    if echo "$data" | grep -q '"entries":\[\]'; then
        echo -e "  ${GREEN}✓ No slow connections detected${NC}"
        echo "    (All peer OSDs are responding in <${threshold}ms)"
    else
        echo -e "  ${YELLOW}⚠ Slow connections detected:${NC}"
        echo ""

        # Try to extract peer OSD and ping time
        # Format varies by Ceph version, so we'll show raw JSON for now
        echo "$data" | python3 -m json.tool 2>/dev/null || echo "$data"
    fi

    echo ""
}

# Get all OSD heartbeat statistics
get_heartbeat_stats() {
    local socket="$1"
    local osd_id=$(get_osd_id "$socket")

    # Get perf dump which includes messenger stats
    ceph daemon "$socket" perf dump 2>/dev/null | \
        python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Look for messenger or heartbeat stats
    if 'osd' in data:
        print(json.dumps(data['osd'], indent=2))
    elif 'objecter' in data:
        print(json.dumps(data['objecter'], indent=2))
    else:
        print(json.dumps(data, indent=2))
except:
    pass
" 2>/dev/null
}

# Display single snapshot
display_snapshot() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Ceph OSD Heartbeat Monitor${NC}"
    echo -e "${CYAN}  $(date)${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    local sockets=$(get_osd_sockets)

    if [[ -z "$sockets" ]]; then
        echo -e "${YELLOW}No OSD admin sockets found${NC}"
        echo "This host may not be running any OSDs, or you may need root access."
        echo ""
        echo "Try: sudo $0"
        return 1
    fi

    local osd_count=$(echo "$sockets" | wc -l)
    echo "Found $osd_count local OSD(s)"
    echo ""

    for socket in $sockets; do
        display_osd_network_data "$socket"
    done

    # Summary
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Summary${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    local total_slow=0
    for socket in $sockets; do
        local data=$(query_osd_network "$socket")
        if ! echo "$data" | grep -q '"entries":\[\]'; then
            ((total_slow++))
        fi
    done

    if [[ $total_slow -eq 0 ]]; then
        echo -e "${GREEN}✓ All OSDs have healthy network connections${NC}"
        echo "  No peer connections exceed the threshold"
    else
        echo -e "${YELLOW}⚠ $total_slow OSD(s) have slow peer connections${NC}"
        echo "  Review details above"
    fi

    echo ""
}

# Continuous monitoring mode
monitor_mode() {
    while true; do
        clear
        display_snapshot
        echo ""
        echo -e "Updates every ${UPDATE_INTERVAL}s | Press Ctrl+C to stop"
        sleep "$UPDATE_INTERVAL"
    done
}

# Export mode - save to files
export_mode() {
    local output_dir="./osd_heartbeat_export_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$output_dir"

    echo "Exporting OSD heartbeat data..."
    echo ""

    local sockets=$(get_osd_sockets)

    if [[ -z "$sockets" ]]; then
        echo "No OSD admin sockets found"
        return 1
    fi

    for socket in $sockets; do
        local osd_id=$(get_osd_id "$socket")
        echo "  Exporting OSD.$osd_id..."

        # Export network data
        query_osd_network "$socket" > "$output_dir/osd.${osd_id}_network.json"

        # Export heartbeat config
        ceph daemon "$socket" config show 2>/dev/null | \
            grep -i heartbeat > "$output_dir/osd.${osd_id}_heartbeat_config.txt" || true

        # Export perf counters
        get_heartbeat_stats "$socket" > "$output_dir/osd.${osd_id}_stats.json"
    done

    echo ""
    echo "Export complete: $output_dir"
    echo ""

    # Create summary
    cat > "$output_dir/README.txt" <<EOF
OSD Heartbeat Data Export
Generated: $(date)

Files:
  osd.X_network.json          - dump_osd_network output
  osd.X_heartbeat_config.txt  - Heartbeat configuration
  osd.X_stats.json            - Performance counters

This data shows Ceph's internal view of OSD-to-OSD network latency.
EOF

    cat "$output_dir/README.txt"
}

# Compare with TCP timestamp data
compare_with_tcp() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Method Comparison${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    cat <<EOF
Ceph Internal Heartbeat vs TCP Timestamp Monitoring:

${BLUE}What Ceph Measures (dump_osd_network):${NC}
  • Application-level heartbeat messages
  • OSD messenger protocol RTT
  • Includes: network + both OSDs' processing time
  • Frequency: Every ~6 seconds (configurable)
  • Threshold: Reports connections >1000ms by default
  • Storage: In-memory, cleared on OSD restart

${BLUE}What ceph-rtt-tracker Measures (TCP timestamps):${NC}
  • Transport-level TCP RTT
  • Any TCP traffic between OSDs
  • Includes: network + TCP stack processing
  • Frequency: Every TCP packet (continuous)
  • Threshold: Custom (currently 0.01-1000ms)
  • Storage: Real-time analysis, not persisted

${YELLOW}Expected Relationship:${NC}
  TCP RTT ≤ Ceph Heartbeat RTT

  • TCP measures lower in the stack
  • Ceph heartbeat includes application processing
  • Both should identify the same "slow" OSD pairs
  • TCP gives more samples, Ceph gives application view

${GREEN}Use Cases:${NC}
  • dump_osd_network: Quick check if Ceph sees issues
  • ceph-rtt-tracker: Detailed analysis, trending, investigation

EOF
}

# Main execution
main() {
    # Check if we have access to admin sockets
    if [[ ! -d /var/run/ceph ]]; then
        echo -e "${RED}Error:${NC} /var/run/ceph not found"
        echo "This host may not be running Ceph, or you need root access."
        exit 1
    fi

    case "$MODE" in
        once)
            display_snapshot
            compare_with_tcp
            ;;
        monitor)
            monitor_mode
            ;;
        export)
            export_mode
            ;;
        *)
            echo "Unknown mode: $MODE"
            usage
            ;;
    esac
}

main
