"""Lectura del estado del equipo.

Módulo de solo consulta: no modifica nada del sistema. Expone los datos en dos
formatos complementarios —una estructura tipada para el resto del programa y un
resumen en prosa para el modelo de lenguaje— de modo que la herramienta
correspondiente se limite a envolverlo.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass

import psutil

__all__ = ["DiskUsage", "SystemSnapshot", "describe_system", "get_snapshot"]

#: Intervalo de muestreo del uso de CPU. psutil necesita dos lecturas separadas
#: en el tiempo para calcular un porcentaje; por debajo de este valor la
#: medición es ruido.
_CPU_SAMPLE_SECONDS = 0.3

_GIB = 1024**3

#: Tamaño mínimo para que una unidad se considere relevante. Descarta unidades
#: virtuales, particiones de arranque y lectores de tarjetas vacíos, que solo
#: añaden ruido al resumen que lee el modelo.
_MIN_DISK_GB = 1.0


@dataclass(frozen=True, slots=True)
class DiskUsage:
    """Ocupación de una unidad de disco."""

    device: str
    total_gb: float
    used_gb: float
    percent: float

    @property
    def free_gb(self) -> float:
        return round(self.total_gb - self.used_gb, 1)


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Fotografía del estado del equipo en un instante dado."""

    os_name: str
    cpu_percent: float
    cpu_cores: int
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    disks: tuple[DiskUsage, ...]
    uptime_seconds: float
    battery_percent: float | None
    battery_plugged: bool | None

    @property
    def ram_free_gb(self) -> float:
        return round(self.ram_total_gb - self.ram_used_gb, 1)


def _to_gb(value: int | float) -> float:
    """Convierte bytes a gibibytes con un decimal."""
    return round(value / _GIB, 1)


def _collect_disks() -> tuple[DiskUsage, ...]:
    """Recopila la ocupación de las unidades montadas.

    Las unidades inaccesibles —lectores de tarjetas vacíos, unidades de red
    caídas, discos cifrados sin montar— se omiten en lugar de interrumpir la
    lectura completa. También se descartan las unidades irrelevantes por
    tamaño, según ``_MIN_DISK_GB``.
    """
    disks: list[DiskUsage] = []

    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue

        if usage.total < _MIN_DISK_GB * _GIB:
            continue

        disks.append(
            DiskUsage(
                device=partition.device.rstrip("\\") or partition.mountpoint,
                total_gb=_to_gb(usage.total),
                used_gb=_to_gb(usage.used),
                percent=usage.percent,
            )
        )

    return tuple(disks)


def _collect_battery() -> tuple[float | None, bool | None]:
    """Lee el estado de la batería, si el equipo dispone de una."""
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError, OSError):
        return None, None

    if battery is None:
        return None, None
    return round(battery.percent, 1), battery.power_plugged


def get_snapshot() -> SystemSnapshot:
    """Obtiene el estado actual del equipo.

    Returns:
        Una instantánea con uso de CPU, memoria, discos, tiempo encendido y
        batería.
    """
    cpu_percent = psutil.cpu_percent(interval=_CPU_SAMPLE_SECONDS)
    memory = psutil.virtual_memory()
    battery_percent, battery_plugged = _collect_battery()

    return SystemSnapshot(
        os_name=f"{platform.system()} {platform.release()}",
        cpu_percent=cpu_percent,
        cpu_cores=psutil.cpu_count(logical=True) or 0,
        ram_total_gb=_to_gb(memory.total),
        ram_used_gb=_to_gb(memory.total - memory.available),
        ram_percent=memory.percent,
        disks=_collect_disks(),
        uptime_seconds=max(0.0, time.time() - psutil.boot_time()),
        battery_percent=battery_percent,
        battery_plugged=battery_plugged,
    )


def _describe_uptime(seconds: float) -> str:
    """Expresa una duración en lenguaje natural, en español."""
    total_minutes = int(seconds // 60)
    days, remainder = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder, 60)

    partes: list[str] = []
    if days:
        partes.append(f"{days} día{'s' if days != 1 else ''}")
    if hours:
        partes.append(f"{hours} hora{'s' if hours != 1 else ''}")
    if minutes and not days:
        partes.append(f"{minutes} minuto{'s' if minutes != 1 else ''}")

    if not partes:
        return "menos de un minuto"
    if len(partes) == 1:
        return partes[0]
    return f"{', '.join(partes[:-1])} y {partes[-1]}"


def describe_system(snapshot: SystemSnapshot | None = None) -> str:
    """Redacta un resumen legible del estado del equipo.

    El destinatario es el modelo de lenguaje, que lo reformulará al responder.
    Por eso el texto es denso en datos y prescinde de florituras.

    Args:
        snapshot: Instantánea a describir. Si se omite, se toma una nueva.

    Returns:
        Un resumen de varias líneas con los datos más relevantes.
    """
    snap = snapshot if snapshot is not None else get_snapshot()

    lineas = [
        f"Sistema operativo: {snap.os_name}",
        f"CPU: {snap.cpu_percent:.0f}% de uso sobre {snap.cpu_cores} núcleos lógicos",
        (
            f"Memoria: {snap.ram_used_gb} GB usados de {snap.ram_total_gb} GB "
            f"({snap.ram_percent:.0f}%), {snap.ram_free_gb} GB disponibles"
        ),
    ]

    for disk in snap.disks:
        lineas.append(
            f"Disco {disk.device}: {disk.used_gb} GB usados de {disk.total_gb} GB "
            f"({disk.percent:.0f}%), {disk.free_gb} GB libres"
        )

    lineas.append(f"Tiempo encendido: {_describe_uptime(snap.uptime_seconds)}")

    if snap.battery_percent is not None:
        estado = "conectado a la corriente" if snap.battery_plugged else "con batería"
        lineas.append(f"Batería: {snap.battery_percent:.0f}%, {estado}")

    return "\n".join(lineas)
