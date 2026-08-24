#!/usr/bin/env bash
# Debug version of live monitor to troubleshoot issues

INTERFACE="${1:-enp0s3}"
PORT_RANGE="${2:-6800-7100}"

echo "=========================================="
echo "Live Monitor Debug"
echo "=========================================="
echo "Interface: $INTERFACE"
echo "Port Range: $PORT_RANGE"
echo ""

# Step 1: Check if running as root
echo "[1/5] Checking permissions..."
if [ "$EUID" -ne 0 ]; then
    echo "  ❌ Not running as root. Please run with sudo."
    exit 1
else
    echo "  ✓ Running as root"
fi

# Step 2: Check if tshark is installed
echo "[2/5] Checking tshark installation..."
if ! command -v tshark &> /dev/null; then
    echo "  ❌ tshark not found. Please install: yum install wireshark-cli"
    exit 1
else
    echo "  ✓ tshark found: $(which tshark)"
    tshark --version | head -1
fi

# Step 3: Check if interface exists
echo "[3/5] Checking network interface..."
if ! ip link show "$INTERFACE" &> /dev/null; then
    echo "  ❌ Interface '$INTERFACE' not found"
    echo "  Available interfaces:"
    ip link show | grep -E "^[0-9]+:" | awk '{print "    - " $2}' | tr -d ':'
    exit 1
else
    echo "  ✓ Interface '$INTERFACE' exists"
    ip link show "$INTERFACE" | grep -E "state (UP|DOWN|UNKNOWN)"
fi

# Step 4: Test tshark capture for 5 seconds
echo "[4/5] Testing packet capture for 5 seconds..."
echo "  Capturing on $INTERFACE, tcp portrange $PORT_RANGE..."
PACKET_COUNT=$(timeout 5 tshark -i "$INTERFACE" -f "tcp portrange $PORT_RANGE" -c 100 2>&1 | grep -c "^")

if [ "$PACKET_COUNT" -eq 0 ]; then
    echo "  ⚠ No packets captured in 5 seconds"
    echo "  Possible causes:"
    echo "    - No OSD traffic on this interface"
    echo "    - Wrong port range"
    echo "    - OSDs not running"
    echo ""
    echo "  Testing ANY TCP traffic for 3 seconds..."
    ANYTCP=$(timeout 3 tshark -i "$INTERFACE" -f "tcp" -c 10 2>&1 | grep -c "^")
    if [ "$ANYTCP" -eq 0 ]; then
        echo "    ❌ No TCP traffic at all on $INTERFACE"
    else
        echo "    ✓ TCP traffic detected, but not on ports $PORT_RANGE"
        echo "    Hint: Check your Ceph port range with: ceph config get osd ms_bind_port_min"
    fi
else
    echo "  ✓ Captured $PACKET_COUNT packets"
fi

# Step 5: Test tshark field extraction
echo "[5/5] Testing tshark field extraction..."
timeout 3 tshark -i "$INTERFACE" -f "tcp portrange $PORT_RANGE" \
    -Y 'tcp.options.timestamp' \
    -T fields \
    -e frame.number \
    -e frame.time_relative \
    -e ip.src \
    -e ip.dst \
    -e tcp.srcport \
    -e tcp.dstport \
    -e tcp.options.timestamp.tsval \
    -e tcp.options.timestamp.tsecr \
    -E separator=, \
    -E quote=d \
    -E occurrence=f \
    2>&1 | head -5 > /tmp/tshark_test.txt

if [ -s /tmp/tshark_test.txt ]; then
    echo "  ✓ Field extraction working"
    echo "  Sample output:"
    head -3 /tmp/tshark_test.txt | sed 's/^/    /'
else
    echo "  ⚠ No output from field extraction"
fi

rm -f /tmp/tshark_test.txt

echo ""
echo "=========================================="
echo "Debug Complete"
echo "=========================================="
echo ""
echo "If all checks passed, try running:"
echo "  sudo ./live_osd_monitor.sh $INTERFACE $PORT_RANGE"
echo ""
