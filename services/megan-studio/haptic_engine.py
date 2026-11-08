"""
Megan Studio Haptic Engine & Adaptive Game Journal (Native BLE Edition)
========================================================================
Host: Outer Heaven (100.116.38.29)
Path: ~/services/megan-studio/haptic_engine.py

Modul-Übersicht:
1. HapticControllerBase:
   - Generische Abstraktionsschicht für Haptik-Hardware (Lovense BLE, Buttplug.io, Mock).
2. LovenseBLEController (Alias: LovenseDirectController):
   - Direkte, native Bluetooth Low Energy (BLE) / GATT Anbindung via Python `bleak`.
   - Automatischer Scan nach Geräten (Präfix "LVS-" oder Service-UUID Suffix "4c5653" / LVS).
   - Dynamische Präfix-Unterstützung (z.B. 5030..., 5a30..., 455a...).
   - Automatische Auflösung der TX-Characteristic (Standard-Suffix: 0002-0000-5000-8000-00805f9b34fb).
   - VibeMate 200-ms-Throttler (asyncio.sleep(0.20), 5 FPS) zum Schutz des Mikrocontrollers und stabiler Bild-Haptik-Synchronisation.
   - Intensity-Wert Caching: Befehle werden nur übertragen, wenn sich der Wert geändert hat.
   - Native ASCII-Befehle: Vibrate:<level>;, Air:Level:<level>;, Stop;
   - Sicherheits-Watchdog: Automatischer Not-Stopp nach 5s ohne Heartbeat bei aktiven Impulsen.
   - Latenzmessung in Millisekunden und automatische Zustandsverwaltung.
3. ButtplugHapticController:
   - Vorbereitete Abstraktion für spätere Buttplug.io WebSocket / Intiface Anbindung.
4. HapticJournalManager:
   - Verwaltet /home/outerheaven/outerheaven-cluster-repo/docs/megan_haptic_journal.md
   - Protokolliert automatisch jede Test-Aktion (Zeitstempel, Event-Kontext, Parameter, Latenz, Feedback von Sebastian).
   - Sektion: "Empfehlungen & Erkenntnisse für Island Adventure" für Liftup-Kurven und visuelle Renders.
"""

import os
import sys
import time
import json
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Generator, AsyncGenerator

# Versuche bleak / dbus_fast zu importieren mit virtuellem Umgebungs-Fallback
try:
    from bleak import BleakScanner, BleakClient
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.exc import BleakError, BleakBluetoothNotAvailableError
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
        from bleak.exc import BleakError, BleakBluetoothNotAvailableError
    except ImportError:
        pass

logger = logging.getLogger("megan-studio.haptics")

def _resolve_journal_path() -> Path:
    target = Path("/home/outerheaven/outerheaven-cluster-repo/docs/megan_haptic_journal.md")
    try:
        if target.parent.exists() or Path("/home/outerheaven").exists():
            return target
    except Exception:
        pass
    repo_docs = Path(__file__).resolve().parent.parent.parent / "docs"
    if repo_docs.exists():
        return repo_docs / "megan_haptic_journal.md"
    return target

# Default-Pfade & Parameter
JOURNAL_PATH = _resolve_journal_path()
LOVENSE_NAME_PREFIX = "LVS-"
LOVENSE_UUID_SUFFIX = "4c5653"  # Hex für 'LVS'
LOVENSE_TX_CHAR_SUFFIX = "0002-0000-5000-8000-00805f9b34fb"
TARGET_DEVICE_MAC = "28:68:47:24:C0:94"
TARGET_DEVICE_NAME = "Dynamo"

# ------------------------------------------------------------------------------
# Timing- & Pacing-Parameter (VibeMate RE 200 ms / 5 FPS & 80 ms BLE Throttler)
# ------------------------------------------------------------------------------
MIN_FRAME_INTERVAL = 0.20       # VibeMate Standard: 200 ms (5 FPS) Frame-Sampling-Takt
BLE_COMMAND_THROTTLE = 0.08     # Hardware-sicheres BLE-Schreibminimum: 80 ms
MIN_THROTTLE_INTERVAL = BLE_COMMAND_THROTTLE  # Abwärtskompatibilität
DEFAULT_WATCHDOG_TIMEOUT = 5.0  # 5s Not-Stopp bei Heartbeat-Verlust
FRAME_UNDERRUN_TIMEOUT = 1.0    # 1.0s Pufferunterlauf Fail-Safe Schwelle


# ==============================================================================
# Native Pattern-Presets (RE Innocent Witches & VibeMate)
# ==============================================================================

HAPTIC_PATTERNS: Dict[str, Dict[str, Any]] = {
    "low": {
        "name": "low",
        "description": "Sanftes Atmen (Level 2-6, 800ms Zyklus, 10-30% Intensität)",
        "min_level": 2,
        "max_level": 6,
        "step_duration": 0.10,  # 8 Schritte à 100ms = 800ms Zyklus
        "levels": [2, 3, 4, 5, 6, 5, 4, 3],
        "cycle_duration": 0.80
    },
    "med": {
        "name": "med",
        "description": "Mittlere Oszillation (Level 8-12, 400ms Zyklus, 40-60% Intensität)",
        "min_level": 8,
        "max_level": 12,
        "step_duration": 0.10,  # 4 Schritte à 100ms = 400ms Zyklus
        "levels": [8, 10, 12, 10],
        "cycle_duration": 0.40
    },
    "high": {
        "name": "high",
        "description": "Schneller Takt (Level 14-18, 200ms Takt, 70-90% Intensität)",
        "min_level": 14,
        "max_level": 18,
        "step_duration": 0.10,  # 2 Schritte à 100ms = 200ms Takt
        "levels": [14, 18],
        "cycle_duration": 0.20
    },
    "crescendo": {
        "name": "crescendo",
        "description": "Lineare Steigerung (Level 2 bis 18 im Sekundentakt)",
        "start_level": 2,
        "end_level": 18,
        "duration_sec": 1.0,
        "step_duration": MIN_FRAME_INTERVAL
    },
    "decrescendo": {
        "name": "decrescendo",
        "description": "Kontrolliertes Abklingen (Level 18 bis 0 im Sekundentakt)",
        "start_level": 18,
        "end_level": 0,
        "duration_sec": 1.0,
        "step_duration": MIN_FRAME_INTERVAL
    }
}


def generate_triangle_wave(
    min_level: int,
    max_level: int,
    step_duration: float = MIN_FRAME_INTERVAL,
    duration_sec: Optional[float] = None
) -> Generator[Tuple[int, float], None, None]:
    """
    Synchroner Generator für oszillierende Dreieckswellen.
    Erzeugt (level, step_duration) Paare basierend auf min_level und max_level.
    """
    min_lvl = max(0, min(20, int(min_level)))
    max_lvl = max(0, min(20, int(max_level)))
    if min_lvl > max_lvl:
        min_lvl, max_lvl = max_lvl, min_lvl

    step_dur = max(BLE_COMMAND_THROTTLE, float(step_duration))

    if min_lvl == max_lvl:
        cycle = [min_lvl]
    else:
        up = list(range(min_lvl, max_lvl + 1))
        down = list(range(max_lvl - 1, min_lvl, -1))
        cycle = up + down

    elapsed = 0.0
    idx = 0
    while duration_sec is None or elapsed < duration_sec:
        lvl = cycle[idx % len(cycle)]
        yield lvl, step_dur
        elapsed += step_dur
        idx += 1


async def triangle_wave(
    min_level: int,
    max_level: int,
    step_duration: float = MIN_FRAME_INTERVAL,
    duration_sec: Optional[float] = None
) -> AsyncGenerator[int, None]:
    """
    Asynchroner Generator für oszillierende Dreieckswellen.
    Liefert den aktuellen Level und wartet step_duration (min 80ms Throttler).
    """
    for lvl, step_dur in generate_triangle_wave(min_level, max_level, step_duration, duration_sec):
        yield lvl
        await asyncio.sleep(step_dur)


def generate_ramp(
    start_level: int,
    end_level: int,
    duration_sec: float = 1.0,
    step_duration: float = MIN_FRAME_INTERVAL
) -> Generator[Tuple[int, float], None, None]:
    """
    Synchroner Generator für lineare Rampen-Interpolation (Crescendo / Decrescendo).
    Erzeugt (level, step_duration) Paare.
    """
    start_lvl = max(0, min(20, int(start_level)))
    end_lvl = max(0, min(20, int(end_level)))
    dur = max(0.1, float(duration_sec))
    step_dur = max(BLE_COMMAND_THROTTLE, float(step_duration))
    steps = max(1, int(round(dur / step_dur)))

    for i in range(steps + 1):
        fraction = i / steps
        current_lvl = int(round(start_lvl + (end_lvl - start_lvl) * fraction))
        yield current_lvl, step_dur


async def ramp(
    start_level: int,
    end_level: int,
    duration_sec: float = 1.0,
    step_duration: float = MIN_FRAME_INTERVAL
) -> AsyncGenerator[int, None]:
    """
    Asynchroner Generator für lineare Rampen-Interpolation.
    Liefert den aktuellen Level und wartet step_duration.
    """
    for lvl, step_dur in generate_ramp(start_level, end_level, duration_sec, step_duration):
        yield lvl
        await asyncio.sleep(step_dur)


# ==============================================================================
# 1. Haptic Controller Abstraktion
# ==============================================================================

class HapticControllerBase(ABC):
    """Abstrakte Basisklasse für Haptik-Controller."""

    @abstractmethod
    async def vibrate(self, strength: int, time_sec: float = 0, context: str = "") -> Dict[str, Any]:
        """Setzt Vibration (0 - 20)."""
        pass

    @abstractmethod
    async def air(self, level: int, time_sec: float = 0, context: str = "") -> Dict[str, Any]:
        """Setzt Luftdruck / Saugen (0 - 4)."""
        pass

    @abstractmethod
    async def stop(self, context: str = "Stop") -> Dict[str, Any]:
        """Stoppt alle haptischen Impulse."""
        pass

    @abstractmethod
    def heartbeat(self) -> None:
        """Aktualisiert den Watchdog-Heartbeat."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Liefert aktuellen Hardware- und Verbindungsstatus."""
        pass

    @abstractmethod
    async def play_pattern(self, name: str, duration_sec: Optional[float] = None, context: str = "Pattern") -> Dict[str, Any]:
        """Spielt ein vordefiniertes Haptik-Pattern ab."""
        pass

    @abstractmethod
    async def play_triangle(self, min_level: int, max_level: int, step_duration: float = MIN_FRAME_INTERVAL, duration_sec: Optional[float] = None, context: str = "Triangle Wave") -> bool:
        """Spielt eine Dreieckswelle ab."""
        pass

    @abstractmethod
    async def play_ramp(self, start_level: int, end_level: int, duration_sec: float = 1.0, step_duration: float = MIN_FRAME_INTERVAL, context: str = "Ramp") -> bool:
        """Spielt eine lineare Rampe ab."""
        pass


# ==============================================================================
# 2. Lovense Native BLE / GATT Controller
# ==============================================================================

class LovenseBLEController(HapticControllerBase):
    """
    Nativer BLE/GATT Controller für Lovense Toys via bleak.
    Ersetzt die lokale HTTP-Netzwerkbridge durch direkte Low-Latency BLE-Kommunikation.
    """

    def __init__(
        self,
        target_name_prefix: str = LOVENSE_NAME_PREFIX,
        target_uuid_suffix: str = LOVENSE_UUID_SUFFIX,
        tx_char_suffix: str = LOVENSE_TX_CHAR_SUFFIX,
        target_mac: Optional[str] = TARGET_DEVICE_MAC,
        min_interval_sec: float = BLE_COMMAND_THROTTLE,
        watchdog_timeout: float = DEFAULT_WATCHDOG_TIMEOUT,
        journal_manager: Optional["HapticJournalManager"] = None
    ):
        self.target_name_prefix = target_name_prefix
        self.target_uuid_suffix = target_uuid_suffix.lower()
        self.tx_char_suffix = tx_char_suffix.lower()
        self.target_mac = target_mac
        self.min_interval_sec = min_interval_sec
        self.watchdog_timeout = watchdog_timeout
        self.journal_manager = journal_manager

        # Zustandsvariablen
        self.current_vibrate: int = 0
        self.current_air: int = 0
        self.is_active: bool = False
        self.last_heartbeat: float = time.time()
        self.last_latency_ms: float = 0.0
        self.last_command: Optional[str] = None
        self.last_context: str = "Init"
        self.last_action_id: Optional[str] = None
        self.connected: bool = False
        self.emergency_stop_triggered: bool = False
        self.last_error: Optional[str] = None

        # BLE Device & GATT Handles
        self.device_name: Optional[str] = None
        self.device_address: Optional[str] = None
        self.tx_characteristic_uuid: Optional[str] = None
        self._client: Optional[BleakClient] = None
        self._tx_char: Optional[BleakGATTCharacteristic] = None

        # 80-ms-Throttler & Concurrency Lock
        self._write_lock = asyncio.Lock()
        self._last_write_time: float = 0.0

        # Background Tasks
        self._running: bool = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self._auto_connect_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Startet den Controller, den Watchdog und die Auto-Connect-Schleife."""
        self._running = True
        self.last_heartbeat = time.time()

        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        if self._auto_connect_task is None or self._auto_connect_task.done():
            self._auto_connect_task = asyncio.create_task(self._auto_connect_loop())

        logger.info(
            f"✔ LovenseBLEController gestartet (Name-Prefix: '{self.target_name_prefix}', "
            f"UUID-Suffix: '{self.target_uuid_suffix}', Throttler: {int(self.min_interval_sec*1000)}ms, "
            f"Watchdog: {self.watchdog_timeout}s)"
        )

    async def stop_worker(self) -> None:
        """Beendet den Controller, Watchdog und die BLE-Verbindung sauber."""
        self._running = False
        if self.is_active:
            try:
                await self.stop(context="Controller Shutdown")
            except Exception:
                pass

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        if self._auto_connect_task and not self._auto_connect_task.done():
            self._auto_connect_task.cancel()
            try:
                await self._auto_connect_task
            except asyncio.CancelledError:
                pass

        if self._client:
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            except Exception as e:
                logger.debug(f"[Lovense BLE] Disconnect error: {e}")
            self._client = None

        self.connected = False
        self._tx_char = None
        logger.info("LovenseBLEController gestoppt.")

    def heartbeat(self) -> None:
        """Erneuert das Heartbeat-Signal. Verhindert den automatischen Not-Stopp."""
        self.last_heartbeat = time.time()
        self.emergency_stop_triggered = False

    # --------------------------------------------------------------------------
    # BLE Scanning & GATT Resolution
    # --------------------------------------------------------------------------

    def is_lovense_device(self, device: BLEDevice, adv_data: Optional[AdvertisementData] = None) -> bool:
        """
        Prüft, ob ein BLE-Gerät ein Lovense Toy ist:
        1. Gerätename beginnt mit 'LVS-', enthält 'Lovense' oder heißt 'Dynamo'
        2. MAC-Adresse entspricht target_mac (Default: '28:68:47:24:C0:94')
        3. Service-UUID enthält Lovense-Suffix '4c5653' ('LVS' in Hex) oder '0023-4bd4-bbd5-a6920e4c5653'
        Unterstützt dynamische Präfixe (z. B. 5030..., 5a30..., 455a...).
        """
        name = (device.name or (adv_data.local_name if adv_data else "") or "").strip()
        if (
            name.upper().startswith(self.target_name_prefix.upper())
            or "LOVENSE" in name.upper()
            or name.lower() == "dynamo"
            or "dynamo" in name.lower()
        ):
            return True

        if device.address and self.target_mac and device.address.upper() == self.target_mac.upper():
            return True

        if adv_data and adv_data.service_uuids:
            for s_uuid in adv_data.service_uuids:
                clean_uuid = s_uuid.lower().replace("-", "")
                if self.target_uuid_suffix in clean_uuid or "4c5653" in clean_uuid or "00234bd4" in clean_uuid:
                    return True

        return False

    def _resolve_tx_characteristic(self, client: BleakClient) -> Optional[BleakGATTCharacteristic]:
        """
        Ermittelt automatisch die TX-Characteristic zum Senden von Befehlen:
        1. Primär: Characteristic mit Lovense TX-Suffix ('0002-0000-5000-8000-00805f9b34fb' oder '0002-0023-4bd4-bbd5-a6920e4c5653')
        2. Fallback: Characteristic mit '0002' und Schreib-Eigenschaft (write / write-without-response)
        3. Generischer Fallback: Beliebige beschreibbare Characteristic in Lovense-Service
        """
        # 1. Standard Lovense TX Suffixe
        for service in client.services:
            for char in service.characteristics:
                c_uuid = char.uuid.lower()
                if (
                    c_uuid.endswith(self.tx_char_suffix)
                    or self.tx_char_suffix in c_uuid
                    or c_uuid.endswith("0002-0023-4bd4-bbd5-a6920e4c5653")
                    or "0002-0023-4bd4-bbd5-a6920e4c5653" in c_uuid
                ):
                    logger.info(f"[Lovense BLE] TX-Characteristic über Standard-Suffix gefunden: {char.uuid}")
                    return char

        # 2. Characteristic mit '0002' und Schreib-Rechten (write / write-without-response)
        for service in client.services:
            for char in service.characteristics:
                c_uuid = char.uuid.lower()
                if "0002" in c_uuid and any(p in char.properties for p in ["write", "write-without-response"]):
                    logger.info(f"[Lovense BLE] TX-Characteristic über 0002-Matching gefunden: {char.uuid}")
                    return char

        # 3. Fallback: Erste beschreibbare Characteristic in einem Lovense-Service
        for service in client.services:
            s_uuid = service.uuid.lower().replace("-", "")
            if self.target_uuid_suffix in s_uuid or "fff0" in s_uuid or "4c5653" in s_uuid or "00234bd4" in s_uuid:
                for char in service.characteristics:
                    if any(p in char.properties for p in ["write", "write-without-response"]):
                        logger.info(f"[Lovense BLE] TX-Characteristic in Lovense-Service gefunden: {char.uuid}")
                        return char

        # 4. Letzter Fallback: Beliebige beschreibbare Characteristic
        for service in client.services:
            for char in service.characteristics:
                if any(p in char.properties for p in ["write", "write-without-response"]):
                    logger.warning(f"[Lovense BLE] Generische beschreibbare Characteristic gewählt: {char.uuid}")
                    return char

        return None

    def _on_disconnected(self, client: BleakClient) -> None:
        """Callback bei unvorhergesehenem Verbindungsverlust."""
        logger.warning(f"⚠️ [Lovense BLE] Verbindung zu {self.device_name} ({self.device_address}) getrennt.")
        self.connected = False
        self.is_active = False
        self._tx_char = None
        self._client = None

    async def scan_and_connect(self) -> bool:
        """
        Führt einen BLE-Scan durch und verbindet sich mit dem ersten gefundenen Lovense-Toy.
        Verwendet deterministisches target_mac Fallback falls Scan fehlschlägt oder das Gerät bereits gekoppelt ist.
        """
        try:
            logger.debug("[Lovense BLE] Starte BLE-Scan nach Lovense Toys...")
            devices_and_adv = await BleakScanner.discover(timeout=4.0, return_adv=True)
            target_device: Optional[Any] = None

            for address, (dev, adv) in devices_and_adv.items():
                if self.is_lovense_device(dev, adv):
                    target_device = dev
                    logger.info(f"✔ Lovense Gerät via BLE entdeckt: '{dev.name}' ({dev.address})")
                    break

            # Deterministisches Fallback auf hinterlegte MAC falls Scan kein Werbegerät erfasst hat
            if not target_device and self.target_mac:
                logger.info(f"[Lovense BLE] Scan fand kein aktives Werbegerät. Verwende deterministisches Fallback auf MAC: {self.target_mac}")
                target_device = self.target_mac

            if not target_device:
                self.connected = False
                return False

            # Verbindungsaufbau via BleakClient
            client = BleakClient(target_device, disconnected_callback=self._on_disconnected)
            await client.connect(timeout=10.0)

            tx_char = self._resolve_tx_characteristic(client)
            if not tx_char:
                dev_label = getattr(target_device, 'name', str(target_device))
                logger.error(f"[Lovense BLE] Keine TX-Characteristic für '{dev_label}' ermittelbar.")
                await client.disconnect()
                self.connected = False
                self.last_error = "TX Characteristic not found"
                return False

            self._client = client
            self._tx_char = tx_char
            self.device_name = getattr(target_device, "name", None) or (TARGET_DEVICE_NAME if str(target_device) == self.target_mac else "Lovense Toy")
            self.device_address = getattr(target_device, "address", str(target_device))
            self.tx_characteristic_uuid = tx_char.uuid
            self.connected = True
            self.last_error = None

            logger.info(
                f"🎉 [Lovense BLE] Erfolgreich verbunden mit '{self.device_name}' ({self.device_address}) | "
                f"TX-UUID: {self.tx_characteristic_uuid}"
            )
            return True

        except BleakBluetoothNotAvailableError as bne:
            self.connected = False
            self.last_error = f"Bluetooth Adapter nicht verfügbar: {bne}"
            logger.debug(f"[Lovense BLE] {self.last_error}")
            return False
        except Exception as e:
            self.connected = False
            self.last_error = f"BLE Scan/Connect Fehler: {e}"
            logger.debug(f"[Lovense BLE] {self.last_error}")
            return False

    async def _auto_connect_loop(self) -> None:
        """
        Hintergrundschleife:
        Versucht periodisch, die BLE-Verbindung aufzubauen oder wiederherzustellen.
        """
        logger.info("Lovense BLE Auto-Connect Loop gestartet.")
        while self._running:
            try:
                if not self.connected or (self._client and not self._client.is_connected):
                    self.connected = False
                    await self.scan_and_connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[Lovense BLE] Fehler in Auto-Connect Loop: {e}")

            # Intervall: Wenn offline alle 6s scannen, wenn online alle 15s prüfen
            sleep_time = 6.0 if not self.connected else 15.0
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    # --------------------------------------------------------------------------
    # 80-ms-Throttler & BLE-Kommandotransmission
    # --------------------------------------------------------------------------

    async def _send_raw_ble_command(self, cmd_str: str) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Sendet einen nativen ASCII-Befehl über BLE an das Toy.
        Erzwingt mindestens 80 ms Abstand (min_interval_sec) zwischen zwei Schreibvorgängen.
        """
        if not self.connected or not self._client or not self._tx_char:
            # Device nicht verbunden -> Graceful Fallback auf Simulation
            return False, 0.0, {"error": "Device not connected via BLE", "simulation": True}

        async with self._write_lock:
            # Strikter 80-ms Throttler
            now = time.monotonic()
            time_since_last = now - self._last_write_time
            if time_since_last < self.min_interval_sec:
                sleep_sec = self.min_interval_sec - time_since_last
                await asyncio.sleep(sleep_sec)

            payload = cmd_str.encode("ascii")
            start_t = time.perf_counter()

            try:
                # write-without-response für minimale Latenz bevorzugen
                write_no_resp = "write-without-response" in self._tx_char.properties
                await self._client.write_gatt_char(
                    self._tx_char,
                    payload,
                    response=not write_no_resp
                )
                self._last_write_time = time.monotonic()
                latency_ms = round((time.perf_counter() - start_t) * 1000, 2)
                self.last_latency_ms = latency_ms
                self.connected = True
                self.last_error = None
                return True, latency_ms, {"command": cmd_str, "status": "sent"}

            except Exception as e:
                self._last_write_time = time.monotonic()
                latency_ms = round((time.perf_counter() - start_t) * 1000, 2)
                self.last_latency_ms = latency_ms
                self.last_error = str(e)
                logger.warning(f"[Lovense BLE] Fehler bei Schreibvorgang '{cmd_str}': {e}")
                if self._client and not self._client.is_connected:
                    self.connected = False
                return False, latency_ms, {"error": str(e)}

    # --------------------------------------------------------------------------
    # Haptische Steuerungsbefehle mit Intensity-Cache
    # --------------------------------------------------------------------------

    async def vibrate(self, strength: int, time_sec: float = 0, context: str = "Vibrate Command") -> Dict[str, Any]:
        """
        Führt Vibration aus (Stufe 0 bis 20).
        Sende Befehle nur dann, wenn sich der Intensity-Wert tatsächlich geändert hat.
        Verwendet den nativen ASCII-Befehl: `Vibrate:<level>;`
        """
        clamped_strength = max(0, min(20, int(strength)))
        self.heartbeat()

        action_id = f"act_{int(time.time()*1000)}"

        # Intensity-Check: Überspringen, falls Wert unverändert
        if clamped_strength == self.current_vibrate and self.is_active == (clamped_strength > 0 or self.current_air > 0):
            logger.debug(f"[Lovense BLE] Vibrate unverändert ({clamped_strength}), überspringe BLE-Write.")
            return {
                "success": True,
                "action_id": self.last_action_id or action_id,
                "command": "vibrate",
                "level": clamped_strength,
                "time_sec": time_sec,
                "latency_ms": 0.0,
                "context": context,
                "connected": self.connected,
                "cached": True
            }

        if clamped_strength > 0:
            cmd_str = f"Vibrate:{clamped_strength};"
        else:
            cmd_str = "Vibrate:0;" if self.current_air > 0 else "Stop;"

        success, latency_ms, raw = await self._send_raw_ble_command(cmd_str)

        self.current_vibrate = clamped_strength
        self.is_active = (self.current_vibrate > 0 or self.current_air > 0)
        self.last_command = f"vibrate:{clamped_strength}"
        self.last_context = context
        self.last_action_id = action_id

        # Im Tagebuch protokollieren
        if self.journal_manager:
            try:
                await self.journal_manager.log_action(
                    action_id=action_id,
                    context=context,
                    command=cmd_str,
                    parameters={"vibrate": clamped_strength, "timeSec": time_sec},
                    latency_ms=latency_ms,
                    success=success or not self.connected
                )
            except Exception as j_err:
                logger.error(f"Fehler beim Schreiben ins Haptic-Journal: {j_err}")

        logger.info(
            f"[Lovense BLE] Vibrate: {clamped_strength}/20 (Befehl: '{cmd_str}', "
            f"Kontext: '{context}', Latenz: {latency_ms}ms, BLE: {'verbunden' if self.connected else 'simuliert'})"
        )
        return {
            "success": success or not self.connected,
            "action_id": action_id,
            "command": "vibrate",
            "level": clamped_strength,
            "time_sec": time_sec,
            "latency_ms": latency_ms,
            "context": context,
            "connected": self.connected,
            "raw": raw
        }

    async def air(self, level: int, time_sec: float = 0, context: str = "Air Command") -> Dict[str, Any]:
        """
        Führt Luftdruck / Saugen aus (Stufe 0 bis 4).
        Sende Befehle nur dann, wenn sich der Intensity-Wert tatsächlich geändert hat.
        Verwendet den nativen ASCII-Befehl: `Air:Level:<level>;`
        """
        clamped_level = max(0, min(4, int(level)))
        self.heartbeat()

        action_id = f"act_{int(time.time()*1000)}"

        # Intensity-Check: Überspringen, falls Wert unverändert
        if clamped_level == self.current_air and self.is_active == (clamped_level > 0 or self.current_vibrate > 0):
            logger.debug(f"[Lovense BLE] Air unverändert ({clamped_level}), überspringe BLE-Write.")
            return {
                "success": True,
                "action_id": self.last_action_id or action_id,
                "command": "air",
                "level": clamped_level,
                "time_sec": time_sec,
                "latency_ms": 0.0,
                "context": context,
                "connected": self.connected,
                "cached": True
            }

        if clamped_level > 0:
            cmd_str = f"Air:Level:{clamped_level};"
        else:
            cmd_str = "Air:Level:0;" if self.current_vibrate > 0 else "Stop;"

        success, latency_ms, raw = await self._send_raw_ble_command(cmd_str)

        self.current_air = clamped_level
        self.is_active = (self.current_vibrate > 0 or self.current_air > 0)
        self.last_command = f"air:{clamped_level}"
        self.last_context = context
        self.last_action_id = action_id

        if self.journal_manager:
            try:
                await self.journal_manager.log_action(
                    action_id=action_id,
                    context=context,
                    command=cmd_str,
                    parameters={"air": clamped_level, "timeSec": time_sec},
                    latency_ms=latency_ms,
                    success=success or not self.connected
                )
            except Exception as j_err:
                logger.error(f"Fehler beim Schreiben ins Haptic-Journal: {j_err}")

        logger.info(
            f"[Lovense BLE] Air:Level: {clamped_level}/4 (Befehl: '{cmd_str}', "
            f"Kontext: '{context}', Latenz: {latency_ms}ms, BLE: {'verbunden' if self.connected else 'simuliert'})"
        )
        return {
            "success": success or not self.connected,
            "action_id": action_id,
            "command": "air",
            "level": clamped_level,
            "time_sec": time_sec,
            "latency_ms": latency_ms,
            "context": context,
            "connected": self.connected,
            "raw": raw
        }

    async def stop(self, context: str = "Stop Command") -> Dict[str, Any]:
        """
        Stoppt alle aktiven Impulse (Vibration & Air).
        Verwendet den nativen ASCII-Befehl: `Stop;`
        """
        self.heartbeat()
        cmd_str = "Stop;"
        success, latency_ms, raw = await self._send_raw_ble_command(cmd_str)

        self.current_vibrate = 0
        self.current_air = 0
        self.is_active = False
        self.last_command = "stop"
        self.last_context = context

        action_id = f"act_{int(time.time()*1000)}"
        self.last_action_id = action_id

        if self.journal_manager:
            try:
                await self.journal_manager.log_action(
                    action_id=action_id,
                    context=context,
                    command=cmd_str,
                    parameters={"vibrate": 0, "air": 0},
                    latency_ms=latency_ms,
                    success=success or not self.connected
                )
            except Exception as j_err:
                logger.error(f"Fehler beim Schreiben ins Haptic-Journal: {j_err}")

        logger.info(f"[Lovense BLE] Stop ausgeführt (Kontext: '{context}', Latenz: {latency_ms}ms)")
        return {
            "success": success or not self.connected,
            "action_id": action_id,
            "command": "stop",
            "level": 0,
            "latency_ms": latency_ms,
            "context": context,
            "connected": self.connected,
            "raw": raw
        }

    # --------------------------------------------------------------------------
    # Native Pattern Presets & Wellenformen
    # --------------------------------------------------------------------------

    async def play_pattern(
        self,
        name: str,
        duration_sec: Optional[float] = None,
        context: str = "Play Pattern"
    ) -> Dict[str, Any]:
        """Spielt ein vordefiniertes Haptik-Pattern (low, med, high, crescendo, decrescendo) ab."""
        pattern_key = name.lower().strip()
        if pattern_key not in HAPTIC_PATTERNS:
            logger.warning(f"Unbekanntes Haptik-Pattern '{name}'. Verfügbar: {list(HAPTIC_PATTERNS.keys())}")
            return {"success": False, "error": f"Unknown pattern '{name}'"}

        cfg = HAPTIC_PATTERNS[pattern_key]
        dur = duration_sec or cfg.get("duration_sec", 3.0)
        logger.info(f"[Lovense BLE] Starte Pattern '{pattern_key}' ({cfg['description']}) für {dur}s...")

        try:
            if "start_level" in cfg and "end_level" in cfg:
                await self.play_ramp(
                    start_level=cfg["start_level"],
                    end_level=cfg["end_level"],
                    duration_sec=dur,
                    step_duration=cfg.get("step_duration", MIN_FRAME_INTERVAL),
                    context=f"{context}:{pattern_key}"
                )
            else:
                levels = cfg.get("levels")
                step_dur = cfg.get("step_duration", MIN_FRAME_INTERVAL)
                if levels:
                    start_t = time.monotonic()
                    idx = 0
                    while time.monotonic() - start_t < dur:
                        lvl = levels[idx % len(levels)]
                        await self.vibrate(lvl, time_sec=step_dur, context=f"{context}:{pattern_key}")
                        await asyncio.sleep(step_dur)
                        idx += 1
                else:
                    await self.play_triangle(
                        min_level=cfg["min_level"],
                        max_level=cfg["max_level"],
                        step_duration=step_dur,
                        duration_sec=dur,
                        context=f"{context}:{pattern_key}"
                    )
            await self.stop(context=f"{context}:{pattern_key}:Finished")
            return {"success": True, "pattern": pattern_key, "duration": dur}
        except asyncio.CancelledError:
            await self.stop(context=f"{context}:{pattern_key}:Cancelled")
            raise
        except Exception as e:
            logger.error(f"[Lovense BLE] Fehler beim Abspielen von Pattern '{pattern_key}': {e}")
            await self.stop(context=f"{context}:{pattern_key}:Error")
            return {"success": False, "error": str(e)}

    async def play_triangle(
        self,
        min_level: int,
        max_level: int,
        step_duration: float = MIN_FRAME_INTERVAL,
        duration_sec: Optional[float] = None,
        context: str = "Triangle Wave"
    ) -> bool:
        """Spielt eine Dreieckswelle direkt auf dem Controller ab."""
        dur = duration_sec or 3.0
        try:
            async for lvl in triangle_wave(min_level, max_level, step_duration, duration_sec=dur):
                await self.vibrate(lvl, time_sec=step_duration, context=context)
            await self.stop(context=f"{context}:Finished")
            return True
        except asyncio.CancelledError:
            await self.stop(context=f"{context}:Cancelled")
            raise
        except Exception as e:
            logger.error(f"[Lovense BLE] Fehler bei play_triangle: {e}")
            await self.stop(context=f"{context}:Error")
            return False

    async def play_ramp(
        self,
        start_level: int,
        end_level: int,
        duration_sec: float = 1.0,
        step_duration: float = MIN_FRAME_INTERVAL,
        context: str = "Ramp"
    ) -> bool:
        """Spielt eine lineare Rampe (Crescendo / Decrescendo) direkt auf dem Controller ab."""
        try:
            async for lvl in ramp(start_level, end_level, duration_sec=duration_sec, step_duration=step_duration):
                await self.vibrate(lvl, time_sec=step_duration, context=context)
            if end_level == 0:
                await self.stop(context=f"{context}:Finished")
            return True
        except asyncio.CancelledError:
            await self.stop(context=f"{context}:Cancelled")
            raise
        except Exception as e:
            logger.error(f"[Lovense BLE] Fehler bei play_ramp: {e}")
            await self.stop(context=f"{context}:Error")
            return False

    # --------------------------------------------------------------------------
    # Sicherheits-Watchdog & Status
    # --------------------------------------------------------------------------

    async def emergency_stop(self, reason: str = "Watchdog Not-Stopp") -> None:
        """
        Löst den automatischen Sicherheits-Not-Stopp aus, wenn für watchdog_timeout
        Sekunden bei aktiven Impulsen kein frischer Heartbeat empfangen wurde.
        """
        self.emergency_stop_triggered = True
        logger.warning(f"🚨 SICHERHEITS-NOT-STOPP AUSGELÖST: {reason}")
        try:
            await self.stop(context=f"EMERGENCY_STOP: {reason}")
        except Exception as e:
            logger.error(f"Fehler bei Not-Stopp-Ausführung: {e}")

        if self.journal_manager:
            try:
                await self.journal_manager.log_event(
                    event_type="EMERGENCY_STOP",
                    message=f"Sicherheits-Watchdog hat nach {self.watchdog_timeout}s ohne Heartbeat gestoppt. Grund: {reason}"
                )
            except Exception:
                pass

    async def on_client_disconnected(self, reason: str = "Client getrennt") -> None:
        """
        Not-Aus & Fail-Safe bei Verbindungsabbruch zum Client:
        Stoppt sofort alle aktiven Lovense-Hardware-Impulse (Stop; / Level 0).
        """
        logger.warning(f"🚨 [Lovense BLE Fail-Safe] Client getrennt ({reason}) -> Not-Aus (Stop;)!")
        await self.emergency_stop(f"Client disconnected: {reason}")

    async def _watchdog_loop(self) -> None:
        """
        Watchdog-Hintergrundschleife:
        Prüft kontinuierlich, ob Impulse aktiv sind und der Heartbeat abgelaufen ist.
        """
        logger.info("Watchdog-Loop aktiv.")
        while self._running:
            try:
                await asyncio.sleep(0.5)
                if self.is_active:
                    time_since_heartbeat = time.time() - self.last_heartbeat
                    if time_since_heartbeat >= self.watchdog_timeout:
                        await self.emergency_stop(
                            f"Heartbeat abgelaufen ({round(time_since_heartbeat, 2)}s >= {self.watchdog_timeout}s)"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unerwarteter Fehler im Watchdog-Loop: {e}")
                await asyncio.sleep(1.0)

    def get_status(self) -> Dict[str, Any]:
        """Liefert vollständigen Status für API-Routen."""
        now = time.time()
        heartbeat_age = round(now - self.last_heartbeat, 2)
        return {
            "success": True,
            "backend": "LovenseBLEController",
            "connection_type": "native_ble_gatt",
            "connected": self.connected,
            "device_name": self.device_name,
            "device_address": self.device_address,
            "tx_characteristic_uuid": self.tx_characteristic_uuid,
            "is_active": self.is_active,
            "current_vibrate": self.current_vibrate,
            "current_air": self.current_air,
            "watchdog_timeout_sec": self.watchdog_timeout,
            "watchdog_active": self._running and (self._watchdog_task is not None and not self._watchdog_task.done()),
            "last_heartbeat_age_sec": heartbeat_age,
            "emergency_stop_triggered": self.emergency_stop_triggered,
            "last_latency_ms": self.last_latency_ms,
            "last_command": self.last_command,
            "last_context": self.last_context,
            "last_action_id": self.last_action_id,
            "last_error": self.last_error,
            "throttle_interval_ms": int(self.min_interval_sec * 1000)
        }


# Abwärtskompatibler Alias
LovenseDirectController = LovenseBLEController


# ==============================================================================
# 3. Buttplug.io Controller Abstraktion (Vorbereitung für spätere Anbindung)
# ==============================================================================

class ButtplugHapticController(HapticControllerBase):
    """
    Vorbereitete Abstraktionsklasse für spätere Buttplug.io / Intiface WebSocket Anbindung.
    Ermöglicht den nahtlosen Wechsel der Backend-Implementierung.
    """

    def __init__(self, websocket_url: str = "ws://127.0.0.1:12345/buttplug"):
        self.websocket_url = websocket_url
        self.connected = False
        self.last_heartbeat = time.time()

    async def connect(self) -> bool:
        logger.info(f"ButtplugHapticController: Verbindung zu {self.websocket_url} vorbereitet.")
        return False

    async def vibrate(self, strength: int, time_sec: float = 0, context: str = "") -> Dict[str, Any]:
        self.heartbeat()
        logger.info(f"[Buttplug.io Stub] Vibrate {strength}")
        return {"success": False, "note": "Buttplug.io Controller ist für spätere Erweiterung vorbereitet."}

    async def air(self, level: int, time_sec: float = 0, context: str = "") -> Dict[str, Any]:
        self.heartbeat()
        logger.info(f"[Buttplug.io Stub] Air {level}")
        return {"success": False, "note": "Buttplug.io Controller ist für spätere Erweiterung vorbereitet."}

    async def stop(self, context: str = "Stop") -> Dict[str, Any]:
        self.heartbeat()
        return {"success": True, "note": "Buttplug.io Stop Stub"}

    def heartbeat(self) -> None:
        self.last_heartbeat = time.time()

    async def play_pattern(self, name: str, duration_sec: Optional[float] = None, context: str = "Pattern") -> Dict[str, Any]:
        self.heartbeat()
        return {"success": False, "note": "Buttplug.io Controller ist für spätere Erweiterung vorbereitet."}

    async def play_triangle(self, min_level: int, max_level: int, step_duration: float = MIN_FRAME_INTERVAL, duration_sec: Optional[float] = None, context: str = "Triangle Wave") -> bool:
        self.heartbeat()
        return False

    async def play_ramp(self, start_level: int, end_level: int, duration_sec: float = 1.0, step_duration: float = MIN_FRAME_INTERVAL, context: str = "Ramp") -> bool:
        self.heartbeat()
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "backend": "ButtplugHapticController",
            "connected": self.connected,
            "url": self.websocket_url,
            "ready": False,
            "status": "Prepared stub for future Buttplug.io integration"
        }


# ==============================================================================
# 4. Adaptives Tagebuch & Empfehlungs-Speicher (Haptic Journal)
# ==============================================================================

class HapticJournalManager:
    """
    Verwaltet das Haptik-Tagebuch unter:
    /home/outerheaven/outerheaven-cluster-repo/docs/megan_haptic_journal.md

    Protokolliert:
    - Jede Test-Aktion: Zeitstempel, Event-Kontext, Parameter (Stufe/Modus), Latenz, Sebastians Feedback
    - Megan-Analyse-Sektion: Empfehlungen & Erkenntnisse für Island Adventure (Liftup-Skala & Visual Renders)
    """

    def __init__(self, journal_file: Path = JOURNAL_PATH):
        self.journal_file = journal_file
        self._lock = asyncio.Lock()
        self._ensure_journal_structure()

    def _ensure_journal_structure(self) -> None:
        """Stellt sicher, dass das Journal existiert und die Grundstruktur besitzt."""
        try:
            if not self.journal_file.parent.exists():
                self.journal_file.parent.mkdir(parents=True, exist_ok=True)

            if not self.journal_file.exists() or self.journal_file.stat().st_size == 0:
                initial_content = """# 📖 Megan Studio – Adaptives Haptik-Tagebuch & Hardware-Journal
Stand: 2026-10-19 | Host: Outer Heaven (100.116.38.29) | Operator: Sebastian

Dieses Journal erfasst vollautomatisch alle haptischen Test-Aktionen, Latenzen, Sicherheitsereignisse sowie Sebastians Feedback. Es dient als kontinuierlicher Lern- und Referenzspeicher für Megan, um adaptive Reaktionsmuster und Haptik-Kurven zu perfektionieren.

---

## 🌸 Megan-Analyse: Empfehlungen & Erkenntnisse für Island Adventure

*In diesem Abschnitt dokumentiert Megan fortlaufend, welche Haptik-Kurven und Schwingungsmuster sich optimal für die spätere Liftup-Skala und visuelle Renders eignen.*

### 1. Kurven-Modellierung für Liftup-Skala (Progression)
- **Sanfter Einstieg (Tension Level 1-5)**:
  - Vibration: Stufe 3 bis 6 (subtile Frequenz, ca. 15-25% Power).
  - Air / Saugimpuls: Stufe 0 oder kurze 500ms Zyklen auf Stufe 1.
  - *Visuelle Render-Korrelation*: Sanfte Pose-Übergänge, ruhige Blicke, dezente Atmungsanimation.
- **Progressiver Aufbau (Tension Level 6-14)**:
  - Vibration: Stufe 8 bis 14 (konstante Schwingung mit ramp-up alle 2 Sekunden).
  - Air / Saugimpuls: Stufe 2 rhythmisch synchronisiert mit Megans Dialogtempo.
  - *Visuelle Render-Korrelation*: Dynamische Beleuchtung, Close-up Focus, beschleunigte Render-Frames.
- **Peak / Climax (Tension Level 15-20)**:
  - Vibration: Stufe 16 bis maximal 20 in 3-Sekunden-Bursts.
  - Air: Stufe 3-4 (Maximaler Unterdruck/Puls zur Akzentuierung von Höhepunkten).
  - *Visuelle Render-Korrelation*: Volle Expression-Intensität, dramatische Shader-Highlights.

### 2. Sicherheits- & Komfort-Vorgaben
- **Watchdog-Garantie**: Jeder Impuls stoppt ausnahmslos nach 5s ohne frischen Heartbeat.
- **Latenz-Ziel**: Native BLE-Reaktionszeiten < 20ms für synchrone Bild-Haptik-Immersion.

---

## ⏱️ Chronologisches Test- und Feedback-Protokoll

| Zeitstempel | Aktion-ID | Kontext | Parameter | Latenz | Hardware-Status | Sebastians Feedback |
|-------------|-----------|---------|-----------|--------|-----------------|---------------------|
"""
                with open(self.journal_file, "w", encoding="utf-8") as f:
                    f.write(initial_content)
                logger.info(f"Haptik-Journal initialisiert unter {self.journal_file}")
        except Exception as e:
            logger.debug(f"[HapticJournalManager] Journal-Initialisierung übersprungen oder nicht schreibbar: {e}")

    async def log_action(
        self,
        action_id: str,
        context: str,
        command: str,
        parameters: Dict[str, Any],
        latency_ms: float,
        success: bool
    ) -> None:
        """Fügt einen neuen Test-Aktions-Eintrag in die Markdown-Tabelle ein."""
        async with self._lock:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            param_str = ", ".join([f"{k}={v}" for k, v in parameters.items()])
            status_badge = "✅ OK" if success else "⚠️ Offline/Sim"
            feedback_placeholder = "*Ausstehend (POST /api/haptics/feedback)*"

            row = f"| {ts} | `{action_id}` | **{context}** | `{command}` ({param_str}) | {latency_ms} ms | {status_badge} | {feedback_placeholder} |\n"

            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(row)

    async def log_event(self, event_type: str, message: str) -> None:
        """Dokumentiert ein spezielles Ereignis (z.B. Watchdog Not-Stopp)."""
        async with self._lock:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"\n> [!WARNING]\n> **{event_type}** ({ts}): {message}\n\n"
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(entry)

    async def record_feedback(
        self,
        rating: Any,
        comment: str,
        context: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> bool:
        """
        Nimmt Sebastians Feedback entgegen und aktualisiert den letzten Eintrag
        oder fügt einen dedizierten Feedback-Block an.
        """
        async with self._lock:
            content = self.journal_file.read_text(encoding="utf-8")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            feedback_str = f"⭐ **{rating}/5**: „{comment}“ ({ts})"

            if action_id and f"`{action_id}`" in content:
                lines = content.splitlines(keepends=True)
                updated_lines = []
                for line in lines:
                    if f"`{action_id}`" in line and "*Ausstehend (POST /api/haptics/feedback)*" in line:
                        updated_lines.append(line.replace("*Ausstehend (POST /api/haptics/feedback)*", feedback_str))
                    else:
                        updated_lines.append(line)
                self.journal_file.write_text("".join(updated_lines), encoding="utf-8")
                return True
            elif "*Ausstehend (POST /api/haptics/feedback)*" in content:
                idx = content.rfind("*Ausstehend (POST /api/haptics/feedback)*")
                if idx != -1:
                    new_content = (
                        content[:idx] +
                        feedback_str +
                        content[idx + len("*Ausstehend (POST /api/haptics/feedback)*"):]
                    )
                    self.journal_file.write_text(new_content, encoding="utf-8")
                    return True

            note = f"\n### 📝 Sebastians Feedback ({ts})\n- **Bewertung**: {rating}/5\n- **Kommentar**: {comment}\n- **Kontext**: {context or 'Allgemein'}\n"
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(note)
            return True


# ==============================================================================
# 5. VibeMate Frame-Haptik-Puffer & Fail-Safe (200 ms / 5 FPS Standard)
# ==============================================================================

class FrameHapticBuffer:
    """
    VibeMate- und Innocent-Witches-kompatibler Frame-Haptik-Puffer.
    Sampling-Takt: 200 ms (5 FPS Standard, MIN_FRAME_INTERVAL = 0.2).

    Fail-Safe-Spezifikation:
    - Bei Pufferunterlauf (Frame-Underrun > 1.0s):
      Sofortiges Absetzen von Stop; (Level 0) an alle verbundenen Lovense-Geräte.
    - Bei Verbindungsabbruch des Clients:
      Puffer wird unmittelbar entleert und Stop; (Level 0) erzwungen.
    - Bei Exceptions im Buffer-Worker:
      Sofortiger Not-Aus (Stop;) und Streaming-Abbruch.
    """

    def __init__(
        self,
        controller: HapticControllerBase,
        sample_interval: float = MIN_FRAME_INTERVAL
    ):
        self.controller = controller
        self.sample_interval = sample_interval
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_streaming: bool = False
        self._lock = asyncio.Lock()

    async def push_frame(self, strength: int, air: int = 0, context: str = "frame") -> None:
        """Schiebt einen neuen Haptik-Frame in den 200-ms-Puffer."""
        await self._queue.put({"strength": strength, "air": air, "context": context})
        async with self._lock:
            if not self._is_streaming:
                self.start_streaming()

    async def push_pattern(self, name: str, duration_sec: Optional[float] = None) -> bool:
        """Schiebt Frames eines vordefinierten Patterns in den Puffer."""
        cfg = HAPTIC_PATTERNS.get(name.lower().strip())
        if not cfg:
            logger.warning(f"[FrameHapticBuffer] Unbekanntes Pattern '{name}'")
            return False
        dur = duration_sec or cfg.get("duration_sec", 3.0)
        if "start_level" in cfg and "end_level" in cfg:
            for lvl, _ in generate_ramp(cfg["start_level"], cfg["end_level"], duration_sec=dur, step_duration=self.sample_interval):
                await self.push_frame(strength=lvl, context=f"pattern:{name}")
        else:
            levels = cfg.get("levels")
            if levels:
                steps = max(1, int(round(dur / self.sample_interval)))
                for i in range(steps):
                    lvl = levels[i % len(levels)]
                    await self.push_frame(strength=lvl, context=f"pattern:{name}")
            else:
                for lvl, _ in generate_triangle_wave(cfg["min_level"], cfg["max_level"], step_duration=self.sample_interval, duration_sec=dur):
                    await self.push_frame(strength=lvl, context=f"pattern:{name}")
        return True

    def start_streaming(self) -> None:
        if not self._worker_task or self._worker_task.done():
            self._is_streaming = True
            self._worker_task = asyncio.create_task(self._process_buffer())

    async def _process_buffer(self) -> None:
        logger.info(f"[FrameHapticBuffer] Starte VibeMate 200ms Frame-Sampling (5 FPS, Takt: {int(self.sample_interval*1000)}ms)...")
        try:
            while self._is_streaming:
                try:
                    # Warte auf den nächsten Frame (bis zu 1.0s Pufferunterlauf-Toleranz)
                    frame = await asyncio.wait_for(self._queue.get(), timeout=FRAME_UNDERRUN_TIMEOUT)
                except asyncio.TimeoutError:
                    # FAIL-SAFE: Pufferunterlauf > 1.0s -> Sofortiges Not-Aus (Stop;)
                    logger.warning(
                        f"🚨 [FrameHapticBuffer] Frame-Underrun (> {FRAME_UNDERRUN_TIMEOUT}s Pufferunterlauf) "
                        f"-> FAILSAFE: Not-Aus (Stop;)!"
                    )
                    await self.controller.stop(context="FAILSAFE_BUFFER_UNDERRUN")
                    self._is_streaming = False
                    break

                strength = frame.get("strength", 0)
                air = frame.get("air", 0)
                ctx = frame.get("context", "frame")

                if strength > 0:
                    await self.controller.vibrate(strength, time_sec=self.sample_interval, context=ctx)
                if air > 0:
                    await self.controller.air(air, time_sec=self.sample_interval, context=ctx)
                if strength == 0 and air == 0:
                    await self.controller.stop(context=ctx)

                await asyncio.sleep(self.sample_interval)
        except asyncio.CancelledError:
            logger.info("[FrameHapticBuffer] Streaming abgebrochen -> Not-Aus.")
            await self.controller.stop(context="FAILSAFE_BUFFER_CANCELLED")
        except Exception as e:
            logger.error(f"[FrameHapticBuffer] Fehler bei Pufferverarbeitung: {e}")
            await self.controller.stop(context="FAILSAFE_BUFFER_ERROR")
        finally:
            self._is_streaming = False

    async def on_client_disconnected(self, reason: str = "Client getrennt") -> None:
        """Fail-Safe bei Verbindungsabbruch: Puffer sofort leeren und Not-Aus an Toys."""
        logger.warning(f"🚨 [FrameHapticBuffer] Client-Verbindungsabbruch ({reason}) -> Puffer leeren & Not-Aus!")
        self._is_streaming = False
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        await self.controller.stop(context=f"FAILSAFE_CLIENT_DISCONNECT: {reason}")


# ==============================================================================
# Globale Singleton-Instanzen
# ==============================================================================

journal_manager = HapticJournalManager()
haptic_controller = LovenseBLEController(journal_manager=journal_manager)
frame_buffer = FrameHapticBuffer(controller=haptic_controller)

