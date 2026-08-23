#!/bin/sh
# Print cumulative receive/transmit byte counters for one interface.
set -eu

interface=${1:?network interface required}
proc_net_dev=${2:-/proc/net/dev}

awk -v interface="$interface" '
    $1 == interface ":" {
        print $2, $10
        found = 1
        exit
    }
    END {
        if (!found) exit 1
    }
' "$proc_net_dev"
