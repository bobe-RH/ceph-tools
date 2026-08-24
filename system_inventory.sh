#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

separator() {
    echo "──────────────────────────────────────────────────────────────"
}

section() {
    echo ""
    separator
    echo -e "${CYAN}${BOLD}  $1${NC}"
    separator
}

field() {
    printf "  ${BOLD}%-22s${NC} %s\n" "$1:" "$2"
}

warn() {
    echo -e "  ${YELLOW}[SKIPPED]${NC} $1"
}

has_cmd() {
    command -v "$1" &>/dev/null
}

if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}WARNING: Not running as root. Some fields (serial numbers, DIMM details, disk serials) will be unavailable.${NC}"
    echo -e "${YELLOW}Re-run with: sudo $0${NC}"
    echo ""
fi

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║              SYSTEM HARDWARE INVENTORY                       ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo -e "  Collected: $(date '+%Y-%m-%d %H:%M:%S %Z')"

section "HOST IDENTITY"
field "Hostname" "$(hostname -s 2>/dev/null || hostname)"
field "FQDN" "$(hostname -f 2>/dev/null || echo 'N/A')"
field "Domain" "$(hostname -d 2>/dev/null || echo 'N/A')"

section "SYSTEM / CHASSIS"
if has_cmd dmidecode && [[ $EUID -eq 0 ]]; then
    field "Manufacturer" "$(dmidecode -s system-manufacturer 2>/dev/null)"
    field "Product Name" "$(dmidecode -s system-product-name 2>/dev/null)"
    field "Serial Number" "$(dmidecode -s system-serial-number 2>/dev/null)"
    field "UUID" "$(dmidecode -s system-uuid 2>/dev/null)"
    field "Chassis Type" "$(dmidecode -s chassis-type 2>/dev/null)"
    field "Chassis Serial" "$(dmidecode -s chassis-serial-number 2>/dev/null)"
else
    if ! has_cmd dmidecode; then
        warn "dmidecode not installed"
    else
        warn "dmidecode requires root"
    fi
    if [[ -f /sys/class/dmi/id/product_serial ]]; then
        field "Serial Number" "$(cat /sys/class/dmi/id/product_serial 2>/dev/null || echo 'N/A')"
    fi
    if [[ -f /sys/class/dmi/id/product_name ]]; then
        field "Product Name" "$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo 'N/A')"
    fi
fi

section "BIOS / FIRMWARE"
if has_cmd dmidecode && [[ $EUID -eq 0 ]]; then
    field "BIOS Vendor" "$(dmidecode -s bios-vendor 2>/dev/null)"
    field "BIOS Version" "$(dmidecode -s bios-version 2>/dev/null)"
    field "BIOS Date" "$(dmidecode -s bios-release-date 2>/dev/null)"
else
    if [[ -f /sys/class/dmi/id/bios_vendor ]]; then
        field "BIOS Vendor" "$(cat /sys/class/dmi/id/bios_vendor 2>/dev/null || echo 'N/A')"
        field "BIOS Version" "$(cat /sys/class/dmi/id/bios_version 2>/dev/null || echo 'N/A')"
        field "BIOS Date" "$(cat /sys/class/dmi/id/bios_date 2>/dev/null || echo 'N/A')"
    else
        warn "Requires root or sysfs access"
    fi
fi

section "MOTHERBOARD"
if has_cmd dmidecode && [[ $EUID -eq 0 ]]; then
    field "Manufacturer" "$(dmidecode -s baseboard-manufacturer 2>/dev/null)"
    field "Product Name" "$(dmidecode -s baseboard-product-name 2>/dev/null)"
    field "Serial Number" "$(dmidecode -s baseboard-serial-number 2>/dev/null)"
    field "Version" "$(dmidecode -s baseboard-version 2>/dev/null)"
else
    warn "Requires root with dmidecode"
fi

section "OPERATING SYSTEM"
if [[ -f /etc/os-release ]]; then
    source /etc/os-release
    field "Distribution" "${NAME:-N/A}"
    field "Version" "${VERSION:-N/A}"
    field "Version ID" "${VERSION_ID:-N/A}"
fi
field "Kernel" "$(uname -r)"
field "Architecture" "$(uname -m)"
field "Uptime" "$(uptime -p 2>/dev/null || uptime)"

section "CPU"
if has_cmd lscpu; then
    field "Model" "$(lscpu | awk -F: '/Model name/ {gsub(/^[ \t]+/,"",$2); print $2}')"
    field "Sockets" "$(lscpu | awk -F: '/^Socket\(s\)/ {gsub(/^[ \t]+/,"",$2); print $2}')"
    field "Cores per Socket" "$(lscpu | awk -F: '/Core\(s\) per socket/ {gsub(/^[ \t]+/,"",$2); print $2}')"
    field "Threads per Core" "$(lscpu | awk -F: '/Thread\(s\) per core/ {gsub(/^[ \t]+/,"",$2); print $2}')"
    total_cpus=$(lscpu | awk -F: '/^CPU\(s\)/ {gsub(/^[ \t]+/,"",$2); print $2; exit}')
    field "Total Logical CPUs" "$total_cpus"
    field "CPU Max MHz" "$(lscpu | awk -F: '/CPU max MHz/ {gsub(/^[ \t]+/,"",$2); print $2}' 2>/dev/null)"
else
    warn "lscpu not available"
    field "Processors" "$(grep -c ^processor /proc/cpuinfo)"
    field "Model" "$(grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)"
fi

section "MEMORY"
total_mem=$(awk '/MemTotal/ {printf "%.1f GB", $2/1024/1024}' /proc/meminfo)
field "Total Memory" "$total_mem"

if has_cmd dmidecode && [[ $EUID -eq 0 ]]; then
    echo ""
    echo "  DIMM Details:"
    dmidecode --type memory 2>/dev/null | awk '
    /^Memory Device$/ { slot=""; size=""; speed=""; serial=""; manuf=""; type="" }
    /^\tLocator:/ { gsub(/^\t*Locator: /,""); slot=$0 }
    /^\tSize:/ { gsub(/^\t*Size: /,""); size=$0 }
    /^\tSpeed:/ { gsub(/^\t*Speed: /,""); speed=$0 }
    /^\tSerial Number:/ { gsub(/^\t*Serial Number: /,""); serial=$0 }
    /^\tManufacturer:/ { gsub(/^\t*Manufacturer: /,""); manuf=$0 }
    /^\tType:/ { gsub(/^\t*Type: /,""); type=$0 }
    /^$/ {
        if (size != "" && size != "No Module Installed" && size != "Not Installed") {
            printf "    %-12s  %-10s  %-8s  %-12s  Serial: %s\n", slot, size, type, speed, serial
        }
    }'
else
    warn "DIMM details require root with dmidecode"
fi

section "NETWORK INTERFACES"
if has_cmd ip; then
    ip -o link show 2>/dev/null | while read -r line; do
        iface=$(echo "$line" | awk -F': ' '{print $2}' | awk '{print $1}')
        mac=$(echo "$line" | grep -oP 'link/ether \K[^ ]+')
        state=$(echo "$line" | grep -oP 'state \K\w+')
        [[ -z "$mac" ]] && continue

        ipaddr=$(ip -4 addr show "$iface" 2>/dev/null | awk '/inet / {print $2}' | paste -sd, -)
        ipaddr=${ipaddr:-none}

        printf "  ${BOLD}%-16s${NC} MAC: %-19s  State: %-6s  IP: %s\n" "$iface" "$mac" "${state:-N/A}" "$ipaddr"
    done
else
    warn "ip command not available"
fi

section "STORAGE"
if has_cmd lsblk; then
    echo "  Block Devices:"
    echo ""
    lsblk -d -o NAME,SIZE,TYPE,MODEL,SERIAL,ROTA,TRAN 2>/dev/null | while IFS= read -r line; do
        echo "    $line"
    done

    if has_cmd smartctl && [[ $EUID -eq 0 ]]; then
        echo ""
        echo "  Disk Details (smartctl):"
        for disk in $(lsblk -dn -o NAME,TYPE 2>/dev/null | awk '$2=="disk" {print $1}'); do
            echo ""
            echo -e "    ${BOLD}/dev/$disk:${NC}"
            smartctl -i "/dev/$disk" 2>/dev/null | grep -E 'Model|Serial|Capacity|Rotation|Form Factor' | while IFS= read -r line; do
                echo "      $line"
            done
        done
    fi
else
    warn "lsblk not available"
    if [[ -d /sys/block ]]; then
        for disk in /sys/block/sd* /sys/block/nvme*; do
            [[ -e "$disk" ]] || continue
            name=$(basename "$disk")
            size=$(awk '{printf "%.1f GB", $1*512/1024/1024/1024}' "$disk/size" 2>/dev/null)
            field "$name" "$size"
        done
    fi
fi

section "PCI DEVICES (Summary)"
if has_cmd lspci; then
    echo "  Network Controllers:"
    lspci 2>/dev/null | grep -i 'network\|ethernet' | while IFS= read -r line; do
        echo "    $line"
    done
    echo ""
    echo "  Storage Controllers:"
    lspci 2>/dev/null | grep -i 'storage\|raid\|sas\|sata\|nvme\|scsi' | while IFS= read -r line; do
        echo "    $line"
    done
    echo ""
    echo "  GPU / Display:"
    lspci 2>/dev/null | grep -i 'vga\|display\|3d' | while IFS= read -r line; do
        echo "    $line"
    done
else
    warn "lspci not available"
fi

echo ""
separator
echo -e "  ${GREEN}${BOLD}Inventory complete.${NC}"
separator
echo ""
