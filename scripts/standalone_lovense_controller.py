#!/usr/bin/env python3
# ==============================================================================
# 🎮 STANDALONE LOVENSE BLE CONTROLLER // AUTONOMOUS HARDWARE ENGINE
# ==============================================================================
# Synchronisation-Standard: Stand 2026-11-08 02:21:30
# Unterstützte Geräte: Dynamo (28:68:47:24:C0:94), Lovense Toys (LVS-*)
# Unterstützte Befehle: Vibrate:<0-20>;, Air:Level:<0-3>;, Stop;
# Throttler: Strikte 80ms Mindestpause zwischen Schreibvorgängen
# Not-Aus: Automatischer Watchdog & Graceful Shutdown
# ==============================================================================

import os
import sys
import time
import argparse
import asyncio
import logging
import subprocess
from typing import Optional, Tuple, Dict, Any, List

# Versuche bleak / dbus_fast zu importieren
try:
    from bleak import BleakScanner, BleakClient
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from bleak.backends.characteristic import BleakGATTCharacteristic
except ImportError:
    venv_candidates = [
        "/home/bigboss/antigravity-env/lib/python3.14/site-packages",
        "/home/bigboss/antigravity-env/lib/python3.12/site-packages",
        "/home/outerheaven/.hermes/.venv/lib/python3.12/site-packages",
    ]
    for p in venv_candidates:
        if os.path.exists(p) and p not in sys.path:
            sys.path.insert(0, p)
    try:
        from bleak import BleakScanner, BleakClient
        from bleak.backends.device import BLEDevice
        from bleak.backends.scanner import AdvertisementData
        from bleak.backends.characteristic import BleakGATTCharacteristic
    except ImportError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("LovenseStandalone")

DEFAULT_MAC = "28:68:47:24:C0:94"
DEFAULT_NAME = "Dynamo"
LOVENSE_NAME_PREFIX = "LVS-"
LOVENSE_UUID_SUFFIX = "4c5653"  # Hex für 'LVS'
LOVENSE_TX_CHAR_SUFFIX = "0002-0000-5000-8000-00805f9b34fb"
THROTTLE_INTERVAL_SEC = 0.08  # 80ms Mindestintervall
DEFAULT_WATCHDOG_TIMEOUT = 5.0  # 5s Watchdog


class StandaloneLovenseController:
    """
    Eigenständige BLE-Steuereinheit für Lovense Toys und Dynamo.
    Benötigt kein Backend, kein FastAPI und keine externen Webdienste.
    Unterstützt sowohl direkte D-Bus/BlueZ Anbindung als auch Bleak.
    """

    def __init__(
        self,
        target_mac: str = DEFAULT_MAC,
        target_name: str = DEFAULT_NAME,
        min_interval_sec: float = THROTTLE_INTERVAL_SEC,
        watchdog_timeout: float = DEFAULT_WATCHDOG_TIMEOUT
    ):
        self.target_mac = target_mac.upper() if target_mac else DEFAULT_MAC
        self.target_name = target_name
        self.min_interval_sec = min_interval_sec
        self.watchdog_timeout = watchdog_timeout

        self._client: Optional[Any] = None
        self._tx_char: Optional[Any] = None
        self._dbus_tx_path: Optional[str] = None
        self._write_lock = asyncio.Lock()
        self._last_write_time: float = 0.0
        self.connected: bool = False
        self.current_vibrate: int = 0
        self.current_air: int = 0
        self.last_heartbeat: float = time.time()
        self._watchdog_task: Optional[asyncio.Task] = None
        self._running: bool = False

    def _find_bluez_tx_path(self) -> Optional[str]:
        """Prüft, ob das Gerät im lokalen BlueZ bereits gekoppelt/verbunden ist und sucht die TX-Characteristic."""
        mac_suffix = self.target_mac.replace(":", "_").upper()
        try:
            tree_output = subprocess.check_output(
                ["busctl", "tree", "org.bluez"],
                text=True, stderr=subprocess.DEVNULL
            )
            char_paths = []
            for line in tree_output.splitlines():
                if f"dev_{mac_suffix}" in line and "/char" in line:
                    parts = line.strip().split()
                    for p in parts:
                        if p.startswith("/org/bluez/"):
                            char_paths.append(p)

            # Prüfe UUIDs der Characteristics
            for c_path in char_paths:
                try:
                    prop_out = subprocess.check_output(
                        ["busctl", "get-property", "org.bluez", c_path, "org.bluez.GattCharacteristic1", "UUID"],
                        text=True, stderr=subprocess.DEVNULL
                    )
                    uuid = prop_out.strip().split()[-1].strip('"').lower()
                    flags_out = subprocess.check_output(
                        ["busctl", "get-property", "org.bluez", c_path, "org.bluez.GattCharacteristic1", "Flags"],
                        text=True, stderr=subprocess.DEVNULL
                    )
                    has_write = "write" in flags_out.lower()
                    if has_write and (
                        uuid.endswith(LOVENSE_TX_CHAR_SUFFIX)
                        or uuid.endswith("0002-0023-4bd4-bbd5-a6920e4c5653")
                        or "455a0002" in uuid
                        or uuid.endswith("fff2")
                    ):
                        return c_path
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def is_matching_device(self, device: Any, adv_data: Optional[Any] = None) -> bool:
        """Prüft Übereinstimmung mit Name, Prefix, MAC oder Lovense GATT UUIDs."""
        name = (getattr(device, "name", None) or (adv_data.local_name if adv_data else "") or "").strip()
        if (
            name.upper().startswith(LOVENSE_NAME_PREFIX)
            or "LOVENSE" in name.upper()
            or name.lower() == self.target_name.lower()
            or self.target_name.lower() in name.lower()
        ):
            return True

        addr = getattr(device, "address", "")
        if addr and addr.upper() == self.target_mac:
            return True

        if adv_data and getattr(adv_data, "service_uuids", None):
            for s_uuid in adv_data.service_uuids:
                clean_uuid = s_uuid.lower().replace("-", "")
                if LOVENSE_UUID_SUFFIX in clean_uuid or "00234bd4" in clean_uuid:
                    return True

        return False

    def _resolve_tx_characteristic(self, client: Any) -> Optional[Any]:
        """Ermittelt die beschreibbare TX-Characteristic des Geräts via Bleak."""
        for service in client.services:
            for char in service.characteristics:
                c_uuid = char.uuid.lower()
                if (
                    c_uuid.endswith(LOVENSE_TX_CHAR_SUFFIX)
                    or LOVENSE_TX_CHAR_SUFFIX in c_uuid
                    or c_uuid.endswith("0002-0023-4bd4-bbd5-a6920e4c5653")
                    or "0002-0023-4bd4-bbd5-a6920e4c5653" in c_uuid
                ):
                    logger.info(f"✔ TX-Characteristic (Standard-Suffix) erkannt: {char.uuid}")
                    return char

        for service in client.services:
            for char in service.characteristics:
                c_uuid = char.uuid.lower()
                if "0002" in c_uuid and any(p in char.properties for p in ["write", "write-without-response"]):
                    logger.info(f"✔ TX-Characteristic (0002-Match) erkannt: {char.uuid}")
                    return char

        for service in client.services:
            s_uuid = service.uuid.lower().replace("-", "")
            if LOVENSE_UUID_SUFFIX in s_uuid or "fff0" in s_uuid or "00234bd4" in s_uuid:
                for char in service.characteristics:
                    if any(p in char.properties for p in ["write", "write-without-response"]):
                        logger.info(f"✔ TX-Characteristic in Lovense-Service gefunden: {char.uuid}")
                        return char

        return None

    async def connect(self, timeout: float = 8.0) -> bool:
        """Verbindet sich direkt mit der Hardware (priorisiert schnellen D-Bus Link bei aktivem Pairing)."""
        self._running = True
        logger.info(f"🔍 Initialisiere Verbindung zu '{self.target_name}' / MAC: {self.target_mac}...")

        # 1. Prüfe schnellen BlueZ D-Bus Pfad (wenn bereits im OS verbunden)
        dbus_char = self._find_bluez_tx_path()
        if dbus_char:
            self._dbus_tx_path = dbus_char
            self.connected = True
            self.last_heartbeat = time.time()
            logger.info(f"✨ Aktiver BlueZ-Link erkannt! Direkter GATT TX-Kanal: {dbus_char}")
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            return True

        # 2. Bleak Verbindung aufbauen falls nicht bereits gekoppelt
        try:
            from bleak import BleakScanner, BleakClient
        except ImportError:
            logger.error("❌ Bleak nicht verfügbar für Remote-Verbindung.")
            return False

        target_device = None
        try:
            devices = await BleakScanner.discover(timeout=3.0, return_adv=True)
            for addr, (dev, adv) in devices.items():
                if self.is_matching_device(dev, adv):
                    target_device = dev
                    logger.info(f"✔ Gerät in BLE Advertisements gefunden: {dev.name} [{dev.address}]")
                    break
        except Exception as e:
            logger.debug(f"Scan-Hinweis: {e}")

        if not target_device:
            logger.info(f"📡 Verwende deterministisches Fallback direkt auf MAC: {self.target_mac}")
            target_device = self.target_mac

        try:
            client = BleakClient(target_device)
            await client.connect(timeout=timeout)
            if not client.is_connected:
                logger.error(f"❌ Verbindung zu {self.target_mac} fehlgeschlagen.")
                return False

            tx_char = self._resolve_tx_characteristic(client)
            if not tx_char:
                logger.error("❌ Keine geeignete TX-Characteristic ermittelt.")
                await client.disconnect()
                return False

            self._client = client
            self._tx_char = tx_char
            self.connected = True
            self.last_heartbeat = time.time()
            logger.info(f"✨ Erfolgreich verbunden via Bleak! TX-Kanal: {tx_char.uuid}")
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            return True

        except Exception as e:
            logger.error(f"❌ Verbindungsfehler: {e}")
            self.connected = False
            return False

    async def disconnect(self):
        """Trennt Verbindung und stoppt Motoren."""
        self._running = False
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()

        if self.connected:
            try:
                await self.stop()
            except Exception:
                pass
            try:
                if self._client and getattr(self._client, "is_connected", False):
                    await self._client.disconnect()
            except Exception:
                pass

        self.connected = False
        self._tx_char = None
        self._dbus_tx_path = None
        self._client = None
        logger.info("🔌 Verbindung sauber getrennt.")

    def heartbeat(self):
        self.last_heartbeat = time.time()

    async def _watchdog_loop(self):
        """Not-Aus Überwachung."""
        while self._running:
            try:
                await asyncio.sleep(1.0)
                if self.connected and (self.current_vibrate > 0 or self.current_air > 0):
                    if time.time() - self.last_heartbeat > self.watchdog_timeout:
                        logger.warning("🚨 WATCHDOG NOT-AUS ausgelöst: Heartbeat überschritten!")
                        await self.stop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog-Fehler: {e}")

    async def send_command(self, cmd_str: str) -> bool:
        """Sendet ASCII-Kommando mit 80ms Mindestabstand."""
        if not self.connected:
            logger.error("❌ Senden fehlgeschlagen: Gerät nicht verbunden.")
            return False

        async with self._write_lock:
            now = time.monotonic()
            elapsed = now - self._last_write_time
            if elapsed < self.min_interval_sec:
                await asyncio.sleep(self.min_interval_sec - elapsed)

            payload = cmd_str.encode("ascii")
            t0 = time.perf_counter()

            # A: D-Bus Direktübertragung (extrem schnell, zuverlässig)
            if self._dbus_tx_path:
                try:
                    bytes_args = [f"0x{b:02x}" for b in payload]
                    cmd = [
                        "busctl", "call", "org.bluez", self._dbus_tx_path,
                        "org.bluez.GattCharacteristic1", "WriteValue", "aya{sv}",
                        str(len(payload))
                    ] + bytes_args + ["0"]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                    )
                    _, stderr = await proc.communicate()
                    self._last_write_time = time.monotonic()
                    latency_ms = (time.perf_counter() - t0) * 1000
                    if proc.returncode == 0:
                        logger.info(f"📤 TX >> '{cmd_str}' ({latency_ms:.1f}ms) [BlueZ D-Bus]")
                        self.heartbeat()
                        return True
                    else:
                        logger.error(f"❌ D-Bus Fehler: {stderr.decode()}")
                        return False
                except Exception as e:
                    logger.error(f"❌ Fehler bei D-Bus Write: {e}")
                    return False

            # B: Bleak Übertragung
            if self._client and self._tx_char:
                try:
                    write_no_resp = "write-without-response" in self._tx_char.properties
                    await self._client.write_gatt_char(
                        self._tx_char,
                        payload,
                        response=not write_no_resp
                    )
                    self._last_write_time = time.monotonic()
                    latency_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"📤 TX >> '{cmd_str}' ({latency_ms:.1f}ms) [Bleak]")
                    self.heartbeat()
                    return True
                except Exception as e:
                    self._last_write_time = time.monotonic()
                    logger.error(f"❌ Bleak Schreibfehler bei '{cmd_str}': {e}")
                    return False

            return False

    async def vibrate(self, level: int) -> bool:
        clamped = max(0, min(20, int(level)))
        self.heartbeat()
        if clamped == self.current_vibrate:
            return True
        cmd = f"Vibrate:{clamped};" if clamped > 0 else "Stop;"
        ok = await self.send_command(cmd)
        if ok:
            self.current_vibrate = clamped
        return ok

    async def air(self, level: int) -> bool:
        clamped = max(0, min(3, int(level)))
        self.heartbeat()
        if clamped == self.current_air:
            return True
        cmd = f"Air:Level:{clamped};" if clamped > 0 else "Stop;"
        ok = await self.send_command(cmd)
        if ok:
            self.current_air = clamped
        return ok

    async def stop(self) -> bool:
        ok = await self.send_command("Stop;")
        self.current_vibrate = 0
        self.current_air = 0
        return ok


async def scan_devices():
    """Scannt nach BLE-Geräten in der Umgebung."""
    print("🔍 Starte BLE-Scan nach Lovense-kompatiblen Geräten...")
    try:
        from bleak import BleakScanner
        devices = await BleakScanner.discover(timeout=4.0, return_adv=True)
        found = 0
        for addr, (dev, adv) in devices.items():
            name = dev.name or adv.local_name or "Unbekannt"
            print(f"  • [{addr}] {name}")
            if (
                name.upper().startswith("LVS-")
                or "LOVENSE" in name.upper()
                or name.lower() == "dynamo"
                or addr.upper() == DEFAULT_MAC
            ):
                print(f"    ⭐ Lovense/Dynamo Match! RSSI: {adv.rssi}dBm")
                found += 1
        print(f"\nScan beendet. {found} kompatible(s) Gerät(e) identifiziert.")
    except Exception as e:
        print(f"Scan-Fehler: {e}")


async def main():
    parser = argparse.ArgumentParser(
        description="🎮 Standalone Lovense BLE Controller (Dynamo / LVS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Beispiele:\n"
               "  python3 standalone_lovense_controller.py --vibrate 10 --duration 3\n"
               "  python3 standalone_lovense_controller.py --stop\n"
               "  python3 standalone_lovense_controller.py --scan\n"
               "  python3 standalone_lovense_controller.py --mac 28:68:47:24:C0:94 --vibrate 15"
    )
    parser.add_argument("--mac", default=DEFAULT_MAC, help=f"Ziel BLE-MAC-Adresse (Standard: {DEFAULT_MAC})")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"Ziel Gerätename (Standard: {DEFAULT_NAME})")
    parser.add_argument("--vibrate", type=int, default=None, help="Vibrationsstufe (0 bis 20)")
    parser.add_argument("--air", type=int, default=None, help="Saugen / Air Level (0 bis 3)")
    parser.add_argument("--duration", type=float, default=0.0, help="Dauer in Sekunden (0 = dauerhaft bis Stop)")
    parser.add_argument("--stop", action="store_true", help="Sendet sofortigen Stop-Befehl")
    parser.add_argument("--scan", action="store_true", help="Führt BLE-Scan aus und beendet")
    parser.add_argument("--interactive", action="store_true", help="Startet interaktive Test-Konsole")

    args = parser.parse_args()

    if args.scan:
        await scan_devices()
        return

    controller = StandaloneLovenseController(target_mac=args.mac, target_name=args.name)

    if not await controller.connect():
        sys.exit(1)

    try:
        if args.stop:
            logger.info("🛑 Sende Not-Aus / Stop...")
            await controller.stop()
            await asyncio.sleep(0.2)
            return

        if args.interactive:
            print("\n🎮 Interaktiver Lovense Modus gestartet (q zum Beenden):")
            print("  Befehle: v<0-20> (Vibration), a<0-3> (Air), s (Stop)")
            loop = asyncio.get_event_loop()
            while True:
                cmd = await loop.run_in_executor(None, input, "lovense> ")
                cmd = cmd.strip().lower()
                if cmd in ["q", "quit", "exit"]:
                    break
                elif cmd == "s" or cmd == "stop":
                    await controller.stop()
                elif cmd.startswith("v"):
                    try:
                        lvl = int(cmd[1:])
                        await controller.vibrate(lvl)
                    except ValueError:
                        print("Ungültiges Format. Beispiel: v10")
                elif cmd.startswith("a"):
                    try:
                        lvl = int(cmd[1:])
                        await controller.air(lvl)
                    except ValueError:
                        print("Ungültiges Format. Beispiel: a2")
            return

        # Direkte Ausführung
        if args.vibrate is not None:
            logger.info(f"💥 Setze Vibration: Stufe {args.vibrate}")
            await controller.vibrate(args.vibrate)

        if args.air is not None:
            logger.info(f"💨 Setze Air Level: Stufe {args.air}")
            await controller.air(args.air)

        if args.duration > 0:
            logger.info(f"⏳ Halte Impuls für {args.duration} Sekunden...")
            start_t = time.time()
            while time.time() - start_t < args.duration:
                controller.heartbeat()
                await asyncio.sleep(0.5)
            logger.info("⏹ Dauer abgelaufen, stoppe Motoren...")
            await controller.stop()
        elif args.vibrate is None and args.air is None:
            logger.info("Keine Aktion angegeben. Verwende --vibrate X oder --help.")

    finally:
        await controller.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAbbruch durch Benutzer.")
