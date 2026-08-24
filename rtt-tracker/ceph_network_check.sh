#!/usr/bin/env bash
# Ceph Network Check - Unified diagnostic and monitoring tool
# Combines diagnostics, validation, and live monitoring

set -u

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
INTERFACE="${1:-}"
MODE="${2:-interactive}"
OUTPUT_BASE="./ceph_network_check_$(date +%Y%m%d_%H%M%S)"

usage() {
    cat <<EOF
${CYAN}Ceph Network Check${NC} - Unified diagnostic and monitoring tool

${GREEN}Usage:${NC}
    $0 [interface] [mode]

${GREEN}Arguments:${NC}
    interface   Network interface (e.g., enp0s3, bond1)
                Leave empty to auto-detect

    mode        Operation mode:
                ${YELLOW}quick${NC}       - Fast diagnostics only (30 seconds)
                ${YELLOW}full${NC}        - Complete diagnostics + validation (2 minutes)
                ${YELLOW}monitor${NC}     - Live monitoring only (continuous)
                ${YELLOW}validate${NC}    - RTT accuracy validation only
                ${YELLOW}interactive${NC} - Ask what to run (default)

${GREEN}Examples:${NC}
    $0                          # Interactive mode, auto-detect interface
    $0 enp0s3 quick             # Quick diagnostics on enp0s3
    $0 bond1 full               # Full diagnostics + validation on bond1
    $0 enp0s3 monitor           # Live monitoring only

${GREEN}What it does:${NC}
    ${BLUE}quick${NC}       → Network diagnostics snapshot
    ${BLUE}full${NC}        → Diagnostics + RTT validation + brief monitoring
    ${BLUE}monitor${NC}     → Real-time OSD latency monitoring (Ctrl+C to stop)
    ${BLUE}validate${NC}    → Compare TCP timestamp RTT vs ICMP ping
    ${BLUE}interactive${NC} → Menu-driven, choose what to run

${GREEN}Output:${NC}
    All results saved to timestamped directory:
    ./ceph_network_check_YYYYMMDD_HHMMSS/
EOF
    exit 0
}

if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    usage
fi

# Detect interface if not provided
detect_interface() {
    local iface=$(ip route | grep default | awk '{print $5}' | head -1)
    if [[ -z "$iface" ]]; then
        iface=$(ip -o addr show | grep -E 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    fi
    echo "$iface"
}

# Interactive menu
interactive_menu() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   Ceph Network Check - Main Menu${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo -e "Interface: ${GREEN}${INTERFACE}${NC}"
    echo ""
    echo "What would you like to do?"
    echo ""
    echo -e "  ${YELLOW}1)${NC} Quick Diagnostics (~30 seconds)"
    echo "     Network health snapshot"
    echo ""
    echo -e "  ${YELLOW}2)${NC} Full Analysis (~2 minutes)"
    echo "     Diagnostics + validation + 30s monitoring"
    echo ""
    echo -e "  ${YELLOW}3)${NC} Live Monitoring (continuous)"
    echo "     Real-time OSD RTT tracking"
    echo ""
    echo -e "  ${YELLOW}4)${NC} RTT Accuracy Validation"
    echo "     Compare measurement methods"
    echo ""
    echo -e "  ${YELLOW}5)${NC} Exit"
    echo ""
    read -p "Select [1-5]: " choice

    case $choice in
        1) MODE="quick" ;;
        2) MODE="full" ;;
        3) MODE="monitor" ;;
        4) MODE="validate" ;;
        5) echo "Exiting."; exit 0 ;;
        *) echo "Invalid choice. Exiting."; exit 1 ;;
    esac
}

# Check requirements
check_requirements() {
    local missing=0

    if ! command -v ceph &>/dev/null; then
        echo -e "${YELLOW}[WARN]${NC} ceph command not found - some checks will be limited"
    fi

    if ! command -v tshark &>/dev/null; then
        echo -e "${RED}[ERROR]${NC} tshark not found - required for monitoring"
        echo "Install with: yum install wireshark"
        missing=1
    fi

    if ! command -v python3 &>/dev/null; then
        echo -e "${RED}[ERROR]${NC} python3 not found"
        missing=1
    fi

    if [[ ! -f "./ceph_network_diag.sh" ]]; then
        echo -e "${RED}[ERROR]${NC} ceph_network_diag.sh not found in current directory"
        missing=1
    fi

    if [[ ! -f "./live_osd_monitor.sh" ]]; then
        echo -e "${RED}[ERROR]${NC} live_osd_monitor.sh not found in current directory"
        missing=1
    fi

    if [[ $missing -eq 1 ]]; then
        echo ""
        echo "Please ensure all required tools are installed and scripts are in current directory."
        exit 1
    fi
}

# Run quick diagnostics
run_quick() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   Running Quick Diagnostics${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    mkdir -p "$OUTPUT_BASE"

    echo -e "${BLUE}[1/1]${NC} Network diagnostics..."
    if [[ -n "$INTERFACE" ]]; then
        ./ceph_network_diag.sh "$INTERFACE" 2>&1 | tee "$OUTPUT_BASE/diagnostics.log"
    else
        ./ceph_network_diag.sh 2>&1 | tee "$OUTPUT_BASE/diagnostics.log"
    fi

    echo ""
    echo -e "${GREEN}✓ Quick diagnostics complete${NC}"
    echo -e "Results saved to: ${CYAN}$OUTPUT_BASE/${NC}"
}

# Run full analysis
run_full() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   Running Full Analysis${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    mkdir -p "$OUTPUT_BASE"

    echo -e "${BLUE}[1/3]${NC} Network diagnostics..."
    if [[ -n "$INTERFACE" ]]; then
        ./ceph_network_diag.sh "$INTERFACE" 2>&1 | tee "$OUTPUT_BASE/diagnostics.log"
    else
        ./ceph_network_diag.sh 2>&1 | tee "$OUTPUT_BASE/diagnostics.log"
    fi

    echo ""
    echo -e "${BLUE}[2/3]${NC} RTT accuracy validation..."
    if [[ $EUID -eq 0 ]]; then
        if [[ -f "./validate_rtt_accuracy.sh" ]]; then
            ./validate_rtt_accuracy.sh "$INTERFACE" 2>&1 | tee "$OUTPUT_BASE/validation.log"

            # Move validation output to our directory
            LATEST_VAL=$(ls -td ./rtt_validation_* 2>/dev/null | head -1)
            if [[ -n "$LATEST_VAL" ]]; then
                mv "$LATEST_VAL"/* "$OUTPUT_BASE/" 2>/dev/null || true
                rmdir "$LATEST_VAL" 2>/dev/null || true
            fi
        else
            echo -e "${YELLOW}[WARN]${NC} validate_rtt_accuracy.sh not found - skipping validation"
        fi
    else
        echo -e "${YELLOW}[WARN]${NC} Not running as root - skipping validation (requires packet capture)"
    fi

    echo ""
    echo -e "${BLUE}[3/3]${NC} 30-second live monitoring sample..."
    if [[ $EUID -eq 0 ]]; then
        echo "Starting monitoring (will stop automatically after 30 seconds)..."
        timeout 30 ./live_osd_monitor.sh "$INTERFACE" 6800-7100 5 2>&1 | tee "$OUTPUT_BASE/monitoring_sample.log" || true
    else
        echo -e "${YELLOW}[WARN]${NC} Not running as root - skipping live monitoring"
    fi

    echo ""
    echo -e "${GREEN}✓ Full analysis complete${NC}"
    echo -e "Results saved to: ${CYAN}$OUTPUT_BASE/${NC}"

    # Quick summary
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   Quick Summary${NC}"
    echo -e "${CYAN}========================================${NC}"

    if [[ -f "$OUTPUT_BASE/diagnostics.log" ]]; then
        echo ""
        echo -e "${BLUE}Issues found:${NC}"
        grep -E "\[CRITICAL\]|\[WARNING\]" "$OUTPUT_BASE/diagnostics.log" | head -10 || echo "  No critical issues detected"
    fi

    if [[ -f "$OUTPUT_BASE/monitoring_sample.log" ]]; then
        echo ""
        echo -e "${BLUE}Top latency connections:${NC}"
        grep -A 3 "Top 10 Connections" "$OUTPUT_BASE/monitoring_sample.log" | tail -15 || true
    fi

    echo ""
    echo "Review full logs in: $OUTPUT_BASE/"
}

# Run live monitoring
run_monitor() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   Live OSD Latency Monitoring${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}[ERROR]${NC} Live monitoring requires root privileges"
        echo "Please run with: sudo $0 $INTERFACE monitor"
        exit 1
    fi

    echo -e "Interface: ${GREEN}${INTERFACE}${NC}"
    echo -e "Press ${YELLOW}Ctrl+C${NC} to stop"
    echo ""

    ./live_osd_monitor.sh "$INTERFACE" 6800-7100 5
}

# Run validation only
run_validate() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}   RTT Accuracy Validation${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    if [[ $EUID -ne 0 ]]; then
        echo -e "${RED}[ERROR]${NC} Validation requires root privileges"
        echo "Please run with: sudo $0 $INTERFACE validate"
        exit 1
    fi

    if [[ ! -f "./validate_rtt_accuracy.sh" ]]; then
        echo -e "${RED}[ERROR]${NC} validate_rtt_accuracy.sh not found"
        exit 1
    fi

    mkdir -p "$OUTPUT_BASE"

    ./validate_rtt_accuracy.sh "$INTERFACE" 2>&1 | tee "$OUTPUT_BASE/validation.log"

    # Move validation output
    LATEST_VAL=$(ls -td ./rtt_validation_* 2>/dev/null | head -1)
    if [[ -n "$LATEST_VAL" ]]; then
        mv "$LATEST_VAL"/* "$OUTPUT_BASE/" 2>/dev/null || true
        rmdir "$LATEST_VAL" 2>/dev/null || true
    fi

    echo ""
    echo -e "${GREEN}✓ Validation complete${NC}"
    echo -e "Results saved to: ${CYAN}$OUTPUT_BASE/${NC}"
}

# Main execution
main() {
    echo -e "${CYAN}"
    cat << "EOF"
╔═══════════════════════════════════════╗
║   Ceph Network Check                  ║
║   Unified Diagnostic & Monitoring     ║
╚═══════════════════════════════════════╝
EOF
    echo -e "${NC}"

    # Auto-detect interface if not provided
    if [[ -z "$INTERFACE" ]]; then
        INTERFACE=$(detect_interface)
        if [[ -z "$INTERFACE" ]]; then
            echo -e "${RED}[ERROR]${NC} Could not auto-detect interface"
            echo "Please specify interface manually: $0 <interface>"
            exit 1
        fi
        echo -e "Auto-detected interface: ${GREEN}${INTERFACE}${NC}"
        echo ""
    fi

    # Check requirements
    check_requirements

    # Interactive menu if mode is interactive
    if [[ "$MODE" == "interactive" ]]; then
        interactive_menu
    fi

    # Execute based on mode
    case "$MODE" in
        quick)
            run_quick
            ;;
        full)
            run_full
            ;;
        monitor)
            run_monitor
            ;;
        validate)
            run_validate
            ;;
        *)
            echo -e "${RED}[ERROR]${NC} Unknown mode: $MODE"
            usage
            ;;
    esac

    echo ""
    echo -e "${GREEN}Done!${NC}"
}

main
