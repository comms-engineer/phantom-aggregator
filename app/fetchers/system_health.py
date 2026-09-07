from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from app.config import settings
from app.fetchers.base import BaseFetcher


class SystemHealthFetcher(BaseFetcher):
    """Captures local system telemetry for operational visibility."""

    name = "system_health"

    def __init__(
        self,
        raw_dir: Path | None = None,
        *,
        data_dir: Path | None = None,
        connectivity_host: str | None = None,
        connectivity_port: int | None = None,
        mesh_interface_names: tuple[str, ...] | None = None,
        name: str | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.data_dir = data_dir or settings.data_dir
        self.connectivity_host = connectivity_host or settings.internet_connectivity_host
        self.connectivity_port = connectivity_port or settings.internet_connectivity_port
        self.mesh_interface_names = tuple(name.lower() for name in (mesh_interface_names or settings.mesh_interface_names))
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        storage = psutil.disk_usage(str(self.data_dir))
        cpu_usage = round(psutil.cpu_percent(interval=0.1), 1)
        cpu_temp = self._read_cpu_temperature()
        interface_stats = psutil.net_if_stats()
        interface_addrs = psutil.net_if_addrs()
        interfaces = self._serialize_interfaces(interface_stats, interface_addrs)
        mesh_interfaces = [details for details in interfaces if self._is_mesh_interface(details["name"])]

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "system": {
                "cpu": {
                    "usage_percent": cpu_usage,
                    "temperature_c": cpu_temp,
                },
                "memory": {
                    "used_percent": round(memory.percent, 1),
                    "available_mb": round(memory.available / (1024 * 1024), 1),
                    "total_mb": round(memory.total / (1024 * 1024), 1),
                },
                "storage": {
                    "path": str(self.data_dir),
                    "free_gb": round(storage.free / (1024 * 1024 * 1024), 2),
                    "total_gb": round(storage.total / (1024 * 1024 * 1024), 2),
                    "used_percent": round(storage.percent, 1),
                },
                "connectivity": {
                    "internet": {
                        "host": self.connectivity_host,
                        "port": self.connectivity_port,
                        "reachable": self._internet_reachable(),
                    },
                    "mesh": {
                        "connected": any(item["is_up"] and item["has_address"] for item in mesh_interfaces),
                        "interfaces": mesh_interfaces,
                    },
                    "interfaces": interfaces,
                },
            },
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _read_cpu_temperature(self) -> float | None:
        try:
            sensors = psutil.sensors_temperatures(fahrenheit=False)
        except (AttributeError, NotImplementedError):
            return None
        for entries in sensors.values():
            for entry in entries:
                current = getattr(entry, "current", None)
                if current is not None:
                    return round(float(current), 1)
        return None

    def _serialize_interfaces(self, interface_stats: Any, interface_addrs: Any) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for name, stats in interface_stats.items():
            addresses = interface_addrs.get(name, [])
            has_address = any(getattr(address, "address", "") and not str(address.address).startswith("127.") for address in addresses)
            serialized.append(
                {
                    "name": name,
                    "is_up": bool(getattr(stats, "isup", False)),
                    "speed_mbps": getattr(stats, "speed", 0),
                    "has_address": has_address,
                }
            )
        return serialized

    def _is_mesh_interface(self, name: str) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in self.mesh_interface_names)

    def _internet_reachable(self) -> bool:
        try:
            with socket.create_connection((self.connectivity_host, self.connectivity_port), timeout=1):
                return True
        except OSError:
            return False
