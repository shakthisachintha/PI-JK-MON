#!/bin/sh
# Reset the Bluetooth controller itself, for when bluetoothd and adapter power-cycles can't revive it
# (e.g. the Pi's built-in chip wedged after an under-voltage: "org.bluez.Error.Failed: Busy", btmgmt timeouts).
# Run as root by ray-vault-bt-reset.service, which the Ray Vault service may start via polkit.
set -u
log() { echo "bt-chip-reset: $*"; }

systemctl stop bluetooth.service
if grep -q '^hci_uart ' /proc/modules; then
  # Built-in UART chip (Pi 3/4/5, Zero W): reloading the driver re-probes it and reloads its firmware.
  log "reloading hci_uart"
  modprobe -r hci_uart || log "modprobe -r hci_uart failed"
  sleep 2
  modprobe hci_uart
elif grep -q '^btusb ' /proc/modules; then
  log "reloading btusb"
  modprobe -r btusb || log "modprobe -r btusb failed"
  sleep 2
  modprobe btusb
else
  log "no hci_uart/btusb module loaded; only restarting bluetoothd"
fi
sleep 3
systemctl start bluetooth.service
sleep 3
if command -v hciconfig >/dev/null && hciconfig 2>/dev/null | grep -q "UP RUNNING"; then
  log "controller up"
else
  log "controller state unknown or down"
fi
