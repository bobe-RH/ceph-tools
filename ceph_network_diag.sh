#!/usr/bin/env bash
# Ceph Network Diagnostics Script
# Comprehensive network health check for Ceph clusters

set -u

# Configuration
INTERFACE="${1:-}"
CEPH_CONF="${2:-/etc/ceph/ceph.conf}"
OUTPUT_DIR="./ceph_diag_$(date +%Y%m%d_%H%M%S)"

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters for issues
CRITICAL_ISSUES=0
WARNINGS=0

usage() {
    cat <<EOF
Usage: $0 [interface] [ceph.conf]

Arguments:
    interface   Network interface to check (e.g., bond1, enp0s3)
                If not specified, will attempt to detect automatically
    ceph.conf   Path to ceph.conf (default: /etc/ceph/ceph.conf)

Examples:
    $0 bond1
    $0 enp0s3 /etc/ceph/ceph.conf

This script performs comprehensive network diagnostics for Ceph clusters:
  - Ceph cluster health and OSD performance
  - Network interface statistics (errors, drops, retransmits)
  - Connectivity tests between OSD hosts
  - TCP connection statistics
  - Optional bandwidth tests with iperf3

Output is saved to: ceph_diag_TIMESTAMP/
EOF
    exit 1
}

if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    usage
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"
REPORT="$OUTPUT_DIR/diagnostic_report.txt"

log_section() {
    local title="$1"
    echo "" | tee -a "$REPORT"
    echo "==================================================================================" | tee -a "$REPORT"
    echo "$title" | tee -a "$REPORT"
    echo "==================================================================================" | tee -a "$REPORT"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$REPORT"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $1" | tee -a "$REPORT"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1" | tee -a "$REPORT"
    ((WARNINGS++))
}

log_critical() {
    echo -e "${RED}[CRITICAL]${NC} $1" | tee -a "$REPORT"
    ((CRITICAL_ISSUES++))
}

# Auto-detect interface if not provided
detect_interface() {
    # Note: Only outputs interface name to stdout, no log messages

    # Method 1: Get interface from default route (most reliable)
    local iface=$(ip route | grep default | awk '{print $5}' | head -1)

    # Method 2: If no default route, try to find interface with IP address
    if [[ -z "$iface" ]]; then
        iface=$(ip -o addr show | grep -E 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    fi

    echo "$iface"
}

# Check if running as root for certain commands
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_warning "Not running as root - some checks will be limited"
        log_info "Run with sudo for complete diagnostics"
        return 1
    fi
    return 0
}

# 1. Ceph Cluster Health
check_ceph_health() {
    log_section "1. CEPH CLUSTER HEALTH"

    if ! command -v ceph &> /dev/null; then
        log_warning "ceph command not found - skipping Ceph health checks"
        return
    fi

    log_info "Cluster status:"
    ceph -s 2>&1 | tee -a "$REPORT"

    echo "" | tee -a "$REPORT"
    log_info "Detailed health:"
    ceph health detail 2>&1 | tee -a "$REPORT"

    # Check for slow requests
    if ceph health detail 2>/dev/null | grep -q "slow requests"; then
        log_critical "Slow requests detected - potential network or OSD issues"
    fi

    # Check for flapping OSDs
    if ceph health detail 2>/dev/null | grep -qE "flapping|marked down"; then
        log_critical "OSDs flapping - likely network instability"
    fi
}

# 2. OSD Performance
check_osd_performance() {
    log_section "2. OSD PERFORMANCE METRICS"

    if ! command -v ceph &> /dev/null; then
        log_warning "ceph command not found - skipping OSD performance checks"
        return
    fi

    log_info "OSD commit and apply latency:"
    ceph osd perf 2>&1 | tee "$OUTPUT_DIR/osd_perf.txt"

    # Parse for high latency OSDs
    local high_latency=$(ceph osd perf 2>/dev/null | awk 'NR>1 && ($2 > 100 || $3 > 100) {print}')
    if [[ -n "$high_latency" ]]; then
        log_warning "OSDs with >100ms latency detected:"
        echo "$high_latency" | tee -a "$REPORT"
    else
        log_ok "All OSDs have acceptable latency"
    fi

    echo "" | tee -a "$REPORT"
    log_info "OSD utilization:"
    ceph osd df tree 2>&1 | head -20 | tee -a "$REPORT"
}

# 3. Network Interface Statistics
check_interface_stats() {
    local iface="$1"
    log_section "3. NETWORK INTERFACE STATISTICS: $iface"

    if [[ -z "$iface" ]]; then
        log_warning "No interface specified - skipping interface checks"
        return
    fi

    if ! ip link show "$iface" &>/dev/null; then
        log_critical "Interface $iface not found"
        return
    fi

    log_info "Interface status:"
    ip addr show "$iface" | tee -a "$REPORT"

    echo "" | tee -a "$REPORT"
    log_info "Interface statistics (errors/drops):"
    ip -s link show "$iface" | tee "$OUTPUT_DIR/interface_stats.txt"

    # Check for RX/TX errors
    local rx_errors=$(ip -s link show "$iface" | grep -A1 "RX:" | tail -1 | awk '{print $3}')
    local tx_errors=$(ip -s link show "$iface" | grep -A1 "TX:" | tail -1 | awk '{print $3}')

    if [[ ${rx_errors:-0} -gt 0 ]] || [[ ${tx_errors:-0} -gt 0 ]]; then
        log_warning "Interface errors detected - RX: $rx_errors, TX: $tx_errors"
    else
        log_ok "No interface errors detected"
    fi

    # ethtool statistics (if available and root)
    if command -v ethtool &>/dev/null && check_root; then
        echo "" | tee -a "$REPORT"
        log_info "Detailed NIC statistics:"
        ethtool -S "$iface" 2>&1 | tee "$OUTPUT_DIR/ethtool_stats.txt"

        # Check for common error indicators
        local error_count=$(ethtool -S "$iface" 2>/dev/null | grep -iE "error|drop|crc|collision" | \
                           awk -F: '{sum+=$2} END {print sum+0}')

        if [[ ${error_count:-0} -gt 100 ]]; then
            log_warning "NIC error counters elevated: $error_count total errors/drops"
        fi
    fi

    # Check link speed
    if command -v ethtool &>/dev/null && check_root; then
        echo "" | tee -a "$REPORT"
        log_info "Link speed and duplex:"
        ethtool "$iface" | grep -E "Speed|Duplex|Link detected" | tee -a "$REPORT"

        local speed=$(ethtool "$iface" 2>/dev/null | grep "Speed:" | awk '{print $2}')
        if [[ "$speed" == "100Mb/s" ]] || [[ "$speed" == "10Mb/s" ]]; then
            log_warning "Interface running at reduced speed: $speed"
        fi
    fi
}

# 4. TCP Statistics
check_tcp_stats() {
    log_section "4. TCP CONNECTION STATISTICS"

    log_info "TCP retransmission statistics:"
    netstat -s 2>/dev/null | grep -i retrans | tee -a "$REPORT"

    local retrans=$(netstat -s 2>/dev/null | grep "segments retransmitted" | awk '{print $1}')
    local segments=$(netstat -s 2>/dev/null | grep "segments sent out" | awk '{print $1}')

    if [[ -n "$retrans" ]] && [[ -n "$segments" ]] && [[ $segments -gt 0 ]]; then
        local retrans_pct=$(awk "BEGIN {printf \"%.2f\", ($retrans/$segments)*100}")
        log_info "Retransmit rate: $retrans_pct%"

        if (( $(echo "$retrans_pct > 1.0" | bc -l) )); then
            log_warning "High retransmission rate: $retrans_pct%"
        fi
    fi

    echo "" | tee -a "$REPORT"
    log_info "Active OSD connections (port 6800-7100):"
    ss -tn | grep -E ':(680[0-9]|68[1-9][0-9]|69[0-9][0-9]|70[0-9][0-9]|7100)' | wc -l | tee -a "$REPORT"

    echo "" | tee -a "$REPORT"
    log_info "Connections with retransmits:"
    ss -ti | grep -B1 "retrans" | grep -E ':(680[0-9]|68[1-9][0-9]|69[0-9][0-9]|70[0-9][0-9]|7100)' | \
        tee "$OUTPUT_DIR/connections_with_retrans.txt" | wc -l | tee -a "$REPORT"
}

# 5. Get OSD hosts
get_osd_hosts() {
    # Note: This function only outputs hostnames to stdout
    # Log messages go to stderr to avoid being captured

    if ! command -v ceph &> /dev/null; then
        return
    fi

    local hosts=$(ceph osd tree 2>/dev/null | grep "host" | awk '{print $4}' | sort -u)

    if [[ -n "$hosts" ]]; then
        echo "$hosts"
    fi
}

# 6. Connectivity Tests
check_connectivity() {
    log_section "5. OSD HOST CONNECTIVITY TESTS"

    log_info "Discovering OSD hosts from Ceph cluster..."
    local hosts=$(get_osd_hosts)

    if [[ -z "$hosts" ]]; then
        log_warning "No OSD hosts found - skipping connectivity checks"
        log_info "Ensure 'ceph osd tree' returns valid data"
        return
    fi

    local host_count=$(echo "$hosts" | wc -w)
    log_info "Found $host_count OSD host(s): $hosts"
    echo "" | tee -a "$REPORT"

    log_info "Testing connectivity to each OSD host..."
    local this_host=$(hostname -s)

    for host in $hosts; do
        if [[ "$host" == "$this_host" ]]; then
            continue
        fi

        log_info "Ping test to $host:"

        # Quick ping test (10 packets)
        if ping -c 10 -i 0.2 "$host" &>/dev/null; then
            local ping_result=$(ping -c 10 -i 0.2 "$host" 2>/dev/null | tail -1)
            echo "  $host: $ping_result" | tee -a "$REPORT"

            # Check for packet loss
            if echo "$ping_result" | grep -q "100% packet loss"; then
                log_critical "$host is unreachable"
            elif echo "$ping_result" | grep -qE "[1-9][0-9]*% packet loss"; then
                log_warning "$host has packet loss"
            fi

            # Check for high latency
            local avg_ms=$(echo "$ping_result" | awk -F'/' '{print $5}')
            if [[ -n "$avg_ms" ]] && (( $(echo "$avg_ms > 10.0" | bc -l) )); then
                log_warning "$host has high ping latency: ${avg_ms}ms"
            fi
        else
            log_critical "Cannot ping $host"
        fi
    done
}

# 7. OSD Network Dumps
check_osd_network_dumps() {
    log_section "6. OSD NETWORK LATENCY (dump_osd_network)"

    if ! command -v ceph &> /dev/null; then
        log_warning "ceph command not found - skipping OSD network dumps"
        return
    fi

    log_info "Checking for high-latency OSD connections (>1000ms threshold)..."

    local found_issues=false

    # Find OSD admin sockets
    local sockets=$(find /var/run/ceph -name "ceph-osd.*.asok" 2>/dev/null)

    if [[ -z "$sockets" ]]; then
        log_info "No local OSD admin sockets found"
        return
    fi

    for sock in $sockets; do
        local osd_id=$(basename "$sock" | sed 's/ceph-osd\.\(.*\)\.asok/\1/')

        local dump=$(ceph --admin-daemon "$sock" dump_osd_network 2>/dev/null)

        if [[ -n "$dump" ]]; then
            echo "$dump" > "$OUTPUT_DIR/osd_network_dump_${osd_id}.json"

            local entry_count=$(echo "$dump" | grep -c "\"")

            if [[ $entry_count -gt 5 ]]; then  # More than just threshold and empty entries
                log_warning "OSD.$osd_id has high-latency connections:"
                echo "$dump" | tee -a "$REPORT"
                found_issues=true
            fi
        fi
    done

    if ! $found_issues; then
        log_ok "No OSD connections exceeding 1000ms threshold"
    fi
}

# 8. Optional: iperf3 bandwidth tests
offer_bandwidth_test() {
    log_section "7. BANDWIDTH TESTING (OPTIONAL)"

    if ! command -v iperf3 &> /dev/null; then
        log_info "iperf3 not installed - skipping bandwidth tests"
        log_info "Install with: yum install iperf3"
        return
    fi

    log_info "iperf3 is available for bandwidth testing"
    log_info "To run bandwidth tests between hosts:"
    echo "" | tee -a "$REPORT"
    echo "  On target host:" | tee -a "$REPORT"
    echo "    iperf3 -s" | tee -a "$REPORT"
    echo "" | tee -a "$REPORT"
    echo "  On source host:" | tee -a "$REPORT"
    echo "    iperf3 -c <target_host> -t 30" | tee -a "$REPORT"
    echo "" | tee -a "$REPORT"
    log_info "Skipping automatic bandwidth tests (requires coordination between hosts)"
}

# 9. Summary
print_summary() {
    log_section "DIAGNOSTIC SUMMARY"

    echo "" | tee -a "$REPORT"
    if [[ $CRITICAL_ISSUES -eq 0 ]] && [[ $WARNINGS -eq 0 ]]; then
        log_ok "No issues detected - network appears healthy"
    else
        if [[ $CRITICAL_ISSUES -gt 0 ]]; then
            log_critical "Found $CRITICAL_ISSUES critical issue(s)"
        fi
        if [[ $WARNINGS -gt 0 ]]; then
            log_warning "Found $WARNINGS warning(s)"
        fi
    fi

    echo "" | tee -a "$REPORT"
    log_info "Full diagnostic output saved to: $OUTPUT_DIR"
    log_info "Main report: $REPORT"

    echo "" | tee -a "$REPORT"
    log_info "Next steps:"
    echo "  1. Review detailed logs in $OUTPUT_DIR/" | tee -a "$REPORT"
    echo "  2. For real-time RTT monitoring, use: ./live_osd_monitor.sh" | tee -a "$REPORT"
    echo "  3. Check interface errors: ethtool -S <interface>" | tee -a "$REPORT"
    echo "  4. Test bandwidth between slow hosts with iperf3" | tee -a "$REPORT"
}

# Main execution
main() {
    log_section "CEPH NETWORK DIAGNOSTICS - $(date)"
    log_info "Hostname: $(hostname)"
    log_info "Kernel: $(uname -r)"

    # Detect interface if not provided
    if [[ -z "$INTERFACE" ]]; then
        log_info "Auto-detecting primary network interface..."
        INTERFACE=$(detect_interface)
        if [[ -n "$INTERFACE" ]]; then
            log_ok "Detected interface: $INTERFACE"
        else
            log_warning "Could not auto-detect interface - some checks will be skipped"
        fi
    else
        log_info "Using specified interface: $INTERFACE"
    fi

    check_root

    check_ceph_health
    check_osd_performance
    check_interface_stats "$INTERFACE"
    check_tcp_stats
    check_connectivity
    check_osd_network_dumps
    offer_bandwidth_test
    print_summary
}

main
