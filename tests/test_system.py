"""Pruebas de la lectura del estado del equipo."""

from __future__ import annotations

from jarvis.computer.system import (
    DiskUsage,
    SystemSnapshot,
    _describe_uptime,
    describe_system,
    get_snapshot,
)


def test_snapshot_reports_plausible_values() -> None:
    snap = get_snapshot()

    assert 0 <= snap.cpu_percent <= 100
    assert snap.cpu_cores >= 1
    assert snap.ram_total_gb > 0
    assert 0 <= snap.ram_percent <= 100
    assert snap.ram_used_gb <= snap.ram_total_gb
    assert snap.uptime_seconds >= 0


def test_free_space_is_derived_consistently() -> None:
    disk = DiskUsage(device="C:", total_gb=500.0, used_gb=340.0, percent=68.0)
    assert disk.free_gb == 160.0


def test_description_mentions_the_key_figures() -> None:
    texto = describe_system(
        SystemSnapshot(
            os_name="Windows 11",
            cpu_percent=12.0,
            cpu_cores=16,
            ram_total_gb=32.0,
            ram_used_gb=14.0,
            ram_percent=44.0,
            disks=(DiskUsage("C:", 500.0, 340.0, 68.0),),
            uptime_seconds=3 * 3600 + 25 * 60,
            battery_percent=None,
            battery_plugged=None,
        )
    )

    assert "Windows 11" in texto
    assert "12%" in texto
    assert "16 núcleos" in texto
    assert "32.0 GB" in texto
    assert "C:" in texto
    assert "3 horas" in texto
    assert "Batería" not in texto  # un equipo de sobremesa no debe inventarse una


def test_description_includes_battery_when_present() -> None:
    texto = describe_system(
        SystemSnapshot(
            os_name="Windows 11",
            cpu_percent=5.0,
            cpu_cores=8,
            ram_total_gb=16.0,
            ram_used_gb=8.0,
            ram_percent=50.0,
            disks=(),
            uptime_seconds=120,
            battery_percent=87.0,
            battery_plugged=False,
        )
    )

    assert "Batería: 87%" in texto
    assert "con batería" in texto


def test_uptime_is_expressed_in_natural_spanish() -> None:
    assert _describe_uptime(30) == "menos de un minuto"
    assert _describe_uptime(60) == "1 minuto"
    assert _describe_uptime(45 * 60) == "45 minutos"
    assert _describe_uptime(3600) == "1 hora"
    assert _describe_uptime(3600 + 25 * 60) == "1 hora y 25 minutos"
    assert _describe_uptime(2 * 86400 + 3 * 3600) == "2 días y 3 horas"


def test_description_of_a_live_snapshot_is_not_empty() -> None:
    texto = describe_system()
    assert "Memoria" in texto
    assert "CPU" in texto
