"""Pruebas del guardia de seguridad y del registro de auditoría."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core.registry import ToolRegistry, ToolSpec, tool
from jarvis.security.audit import AuditEntry, AuditLog
from jarvis.security.guard import (
    Decision,
    Guard,
    Verdict,
    dangerous_command_policy,
    path_jail_policy,
)
from jarvis.security.risk import Risk


@pytest.fixture
def reg() -> ToolRegistry:
    return ToolRegistry()


def _spec(reg: ToolRegistry, risk: Risk, category: str = "system") -> ToolSpec:
    """Declara una herramienta de ejemplo y devuelve su especificación."""

    @tool(risk=risk, category=category, name=f"prueba_{risk}_{category}", registry=reg)
    def herramienta(texto: str = "") -> str:
        """Herramienta de ejemplo para las pruebas.

        Args:
            texto: Contenido irrelevante.
        """
        return texto

    return reg.get(f"prueba_{risk}_{category}")


# --------------------------------------------------------------------------- #
# Riesgo estático
# --------------------------------------------------------------------------- #


def test_safe_tools_are_allowed(reg: ToolRegistry) -> None:
    veredicto = Guard().evaluate(_spec(reg, Risk.SAFE), {})
    assert veredicto.decision is Decision.ALLOW
    assert veredicto.allowed


def test_confirm_tools_require_confirmation(reg: ToolRegistry) -> None:
    veredicto = Guard().evaluate(_spec(reg, Risk.CONFIRM), {})
    assert veredicto.needs_confirmation


def test_blocked_tools_are_denied(reg: ToolRegistry) -> None:
    veredicto = Guard().evaluate(_spec(reg, Risk.BLOCKED), {})
    assert veredicto.denied


# --------------------------------------------------------------------------- #
# Comandos destructivos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "peligroso",
    [
        "format c:",
        "del /s C:\\Windows",
        "rd /s /q algo",
        "diskpart",
        "vssadmin delete shadows /all",
        "reg delete HKLM\\Software",
        "shutdown /r /t 0",
        "Remove-Item C:\\ -Recurse -Force",
        "Set-ExecutionPolicy Unrestricted",
        "rm -rf /",
        "cipher /w:C",
    ],
)
def test_destructive_commands_are_denied(reg: ToolRegistry, peligroso: str) -> None:
    """El patrón se detecta con independencia de la herramienta que lo reciba."""
    guard = Guard.with_default_policies()
    veredicto = guard.evaluate(_spec(reg, Risk.SAFE), {"texto": peligroso})
    assert veredicto.denied
    assert veredicto.policy == "comandos destructivos"


@pytest.mark.parametrize(
    "inocuo",
    [
        "chrome",
        "abre el bloc de notas",
        "formato de fecha",
        "delta de temperatura",
        "reglas del juego",
        "shut down the music",
    ],
)
def test_harmless_text_is_not_flagged(reg: ToolRegistry, inocuo: str) -> None:
    """Un detector que salta con «formato» o «delta» sería inservible."""
    guard = Guard.with_default_policies()
    assert guard.evaluate(_spec(reg, Risk.SAFE), {"texto": inocuo}).allowed


def test_nested_arguments_are_inspected(reg: ToolRegistry) -> None:
    """Esconder el comando dentro de una lista no debe eludir la revisión."""
    guard = Guard.with_default_policies()
    argumentos = {"texto": {"pasos": ["abrir algo", "format c:"]}}
    assert guard.evaluate(_spec(reg, Risk.SAFE), argumentos).denied


# --------------------------------------------------------------------------- #
# Jaula de rutas
# --------------------------------------------------------------------------- #


def test_paths_outside_the_jail_are_denied(reg: ToolRegistry, tmp_path: Path) -> None:
    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = _spec(reg, Risk.SAFE, category="files")

    veredicto = guard.evaluate(spec, {"texto": "C:\\Windows\\System32\\config"})
    assert veredicto.denied
    assert veredicto.policy == "jaula de rutas"


def test_paths_inside_the_jail_are_allowed(reg: ToolRegistry, tmp_path: Path) -> None:
    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = _spec(reg, Risk.SAFE, category="files")

    interno = tmp_path / "subcarpeta" / "informe.txt"
    assert guard.evaluate(spec, {"texto": str(interno)}).allowed


def test_traversal_cannot_escape_the_jail(reg: ToolRegistry, tmp_path: Path) -> None:
    """«..» debe resolverse antes de comparar, no compararse tal cual."""
    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = _spec(reg, Risk.SAFE, category="files")

    fuga = tmp_path / ".." / ".." / "etc" / "passwd"
    assert guard.evaluate(spec, {"texto": str(fuga)}).denied


def test_the_jail_only_applies_to_its_categories(
    reg: ToolRegistry, tmp_path: Path
) -> None:
    """Abrir un programa instalado en «Archivos de programa» es legítimo."""
    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = _spec(reg, Risk.SAFE, category="apps")
    assert guard.evaluate(spec, {"texto": "C:\\Program Files\\App\\app.exe"}).allowed


def test_non_path_text_is_ignored_by_the_jail(
    reg: ToolRegistry, tmp_path: Path
) -> None:
    guard = Guard(policies=[path_jail_policy(allowed_roots=[tmp_path])])
    spec = _spec(reg, Risk.SAFE, category="files")
    assert guard.evaluate(spec, {"texto": "informe trimestral"}).allowed


# --------------------------------------------------------------------------- #
# Combinación de veredictos
# --------------------------------------------------------------------------- #


def test_policies_can_only_tighten_the_verdict(reg: ToolRegistry) -> None:
    """Una política permisiva no debe poder abrir lo que el riesgo cerró."""

    def politica_permisiva(spec: ToolSpec, arguments: dict) -> Verdict:
        return Verdict(Decision.ALLOW, "todo me parece bien", "permisiva")

    guard = Guard(policies=[politica_permisiva])
    assert guard.evaluate(_spec(reg, Risk.BLOCKED), {}).denied


def test_the_strictest_policy_prevails(reg: ToolRegistry) -> None:
    def confirmar(spec: ToolSpec, arguments: dict) -> Verdict:
        return Verdict(Decision.CONFIRM, "por si acaso", "cautelosa")

    def denegar(spec: ToolSpec, arguments: dict) -> Verdict:
        return Verdict(Decision.DENY, "ni hablar", "estricta")

    guard = Guard(policies=[confirmar, denegar])
    veredicto = guard.evaluate(_spec(reg, Risk.SAFE), {})
    assert veredicto.denied
    assert veredicto.policy == "estricta"


def test_policies_returning_none_are_ignored(reg: ToolRegistry) -> None:
    guard = Guard(policies=[lambda spec, args: None])
    assert guard.evaluate(_spec(reg, Risk.SAFE), {}).allowed


def test_the_verdict_explains_itself(reg: ToolRegistry) -> None:
    veredicto = dangerous_command_policy(_spec(reg, Risk.SAFE), {"t": "format c:"})
    assert veredicto is not None
    assert "formatear una unidad" in veredicto.reason


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #


def _entrada(**overrides: object) -> AuditEntry:
    base: dict = {
        "tool": "set_volume",
        "arguments": {"level": 70},
        "risk": "safe",
        "decision": "allow",
        "reason": "riesgo declarado",
        "executed": True,
        "succeeded": True,
        "duration_ms": 12.34,
    }
    base.update(overrides)
    return AuditEntry(**base)  # type: ignore[arg-type]


def test_entries_are_written_one_per_line(tmp_path: Path) -> None:
    log = AuditLog(path=tmp_path / "audit.jsonl")

    assert log.record(_entrada())
    assert log.record(_entrada(tool="open_program", arguments={"name": "chrome"}))

    lineas = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    assert json.loads(lineas[0])["tool"] == "set_volume"
    assert json.loads(lineas[1])["tool"] == "open_program"


def test_denied_calls_are_also_recorded(tmp_path: Path) -> None:
    """Lo que se impidió es tan relevante como lo que se ejecutó."""
    log = AuditLog(path=tmp_path / "audit.jsonl")
    log.record(_entrada(decision="deny", executed=False, succeeded=None))

    entrada = log.read_all()[0]
    assert entrada["decision"] == "deny"
    assert entrada["executed"] is False
    assert "succeeded" not in entrada


def test_entries_carry_a_timestamp(tmp_path: Path) -> None:
    log = AuditLog(path=tmp_path / "audit.jsonl")
    log.record(_entrada())
    assert log.read_all()[0]["timestamp"].endswith("+00:00")


def test_long_values_are_truncated(tmp_path: Path) -> None:
    log = AuditLog(path=tmp_path / "audit.jsonl")
    log.record(_entrada(arguments={"texto": "x" * 2000}))

    guardado = log.read_all()[0]["arguments"]["texto"]
    assert len(guardado) < 600
    assert guardado.endswith("(+1500)")


def test_reading_a_missing_log_returns_nothing(tmp_path: Path) -> None:
    assert AuditLog(path=tmp_path / "no-existe.jsonl").read_all() == []


def test_corrupt_lines_do_not_invalidate_the_log(tmp_path: Path) -> None:
    """Un apagado repentino puede dejar una línea a medias."""
    destino = tmp_path / "audit.jsonl"
    log = AuditLog(path=destino)
    log.record(_entrada())
    with destino.open("a", encoding="utf-8") as archivo:
        archivo.write('{"tool": "incompl\n')
    log.record(_entrada(tool="get_system_info"))

    entradas = log.read_all()
    assert [e["tool"] for e in entradas] == ["set_volume", "get_system_info"]


def test_a_write_failure_never_raises(tmp_path: Path) -> None:
    """La auditoría no debe poder tumbar una tarea del usuario."""
    obstaculo = tmp_path / "obstaculo"
    obstaculo.write_text("soy un archivo, no una carpeta")

    log = AuditLog(path=obstaculo / "audit.jsonl")
    assert log.record(_entrada()) is False


def test_the_log_can_be_cleared(tmp_path: Path) -> None:
    log = AuditLog(path=tmp_path / "audit.jsonl")
    log.record(_entrada())
    log.clear()
    assert log.read_all() == []
