"""Pruebas de la memoria del asistente.

La base de datos se crea en un archivo temporal, de modo que ninguna prueba
toca la memoria real del usuario.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.database import MemoryDatabase
from jarvis.memory.manager import RELEVANCE_THRESHOLD, MemoryManager


@pytest.fixture
def memoria(tmp_path: Path) -> MemoryManager:
    return MemoryManager(database=MemoryDatabase(path=tmp_path / "memoria.db"))


# --------------------------------------------------------------------------- #
# Guardar
# --------------------------------------------------------------------------- #


def test_a_fact_is_remembered(memoria: MemoryManager) -> None:
    respuesta = memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")

    assert "Anotado" in respuesta
    assert memoria.database.count() == 1


def test_remembering_the_same_subject_updates_it(memoria: MemoryManager) -> None:
    """Recordar dos veces lo mismo debe corregirlo, no duplicarlo."""
    memoria.remember("proyecto de python", "está en C:/viejo")
    respuesta = memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")

    assert "Actualizado" in respuesta
    assert memoria.database.count() == 1
    assert "D:/Proyectos" in memoria.database.all_facts()[0].content


def test_an_incomplete_fact_is_refused(memoria: MemoryManager) -> None:
    assert "necesito saber" in memoria.remember("", "algo")
    assert "necesito saber" in memoria.remember("algo", "   ")
    assert memoria.database.count() == 0


def test_the_subject_is_normalised(memoria: MemoryManager) -> None:
    memoria.remember("  proyecto   de   python  ", "una ruta")
    assert memoria.database.all_facts()[0].subject == "proyecto de python"


def test_memory_can_be_disabled(tmp_path: Path) -> None:
    apagada = MemoryManager(
        database=MemoryDatabase(path=tmp_path / "m.db"), enabled=False
    )
    assert "desactivada" in apagada.remember("algo", "otra cosa")
    assert apagada.database.count() == 0


# --------------------------------------------------------------------------- #
# Recuperar
# --------------------------------------------------------------------------- #


def test_a_relevant_fact_is_recalled(memoria: MemoryManager) -> None:
    memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")
    memoria.remember("cumpleaños", "el 3 de marzo")

    recordados = memoria.recall("abre mi proyecto de python")

    assert len(recordados) == 1
    assert "JARVIS" in recordados[0].content


def test_recall_tolerates_imprecision(memoria: MemoryManager) -> None:
    """El usuario no repite las palabras exactas con las que se guardó."""
    memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")
    assert memoria.recall("mi proyecto")


def test_the_content_is_searchable_too(memoria: MemoryManager) -> None:
    """A veces se pregunta por un nombre que solo aparece dentro del hecho."""
    memoria.remember("trabajo", "soy desarrollador en una empresa de videojuegos")
    assert memoria.recall("videojuegos")


def test_unrelated_facts_are_not_recalled(memoria: MemoryManager) -> None:
    """Inyectar memoria que no viene al caso desperdicia el contexto."""
    memoria.remember("cumpleaños", "el 3 de marzo")
    assert memoria.recall("sube el volumen") == []


def test_recall_is_capped(memoria: MemoryManager) -> None:
    for i in range(20):
        memoria.remember(f"proyecto numero {i}", "una ruta de proyecto")

    assert len(memoria.recall("proyecto", limit=5)) <= 5


def test_the_context_is_empty_without_relevant_facts(
    memoria: MemoryManager,
) -> None:
    memoria.remember("cumpleaños", "el 3 de marzo")
    assert memoria.context_for("sube el volumen") == ""


def test_the_context_names_the_relevant_facts(memoria: MemoryManager) -> None:
    memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")
    contexto = memoria.context_for("abre mi proyecto de python")

    assert "D:/Proyectos/JARVIS" in contexto
    assert "cumpleaños" not in contexto


def test_a_disabled_memory_recalls_nothing(tmp_path: Path) -> None:
    gestor = MemoryManager(database=MemoryDatabase(path=tmp_path / "m.db"))
    gestor.remember("proyecto", "una ruta")
    gestor.enabled = False
    assert gestor.recall("proyecto") == []


# --------------------------------------------------------------------------- #
# Olvidar
# --------------------------------------------------------------------------- #


def test_a_fact_is_forgotten(memoria: MemoryManager) -> None:
    memoria.remember("cumpleaños", "el 3 de marzo")
    assert "Olvidado" in memoria.forget("cumpleaños")
    assert memoria.database.count() == 0


def test_forgetting_tolerates_an_approximate_name(memoria: MemoryManager) -> None:
    """El usuario no recuerda con qué palabras exactas se guardó."""
    memoria.remember("proyecto de python", "una ruta")
    assert "Olvidado" in memoria.forget("proyecto python")
    assert memoria.database.count() == 0


def test_forgetting_something_unknown_is_reported(memoria: MemoryManager) -> None:
    assert "No tengo nada anotado" in memoria.forget("inventado")


def test_an_ambiguous_subject_is_not_guessed(memoria: MemoryManager) -> None:
    """Borrar la anotación equivocada es irreversible: mejor preguntar."""
    memoria.remember("proyecto alfa", "una ruta")
    memoria.remember("proyecto beta", "otra ruta")

    respuesta = memoria.forget("proyecto")

    assert "varias anotaciones" in respuesta
    assert memoria.database.count() == 2


def test_the_whole_memory_can_be_cleared(memoria: MemoryManager) -> None:
    memoria.remember("uno", "a")
    memoria.remember("dos", "b")

    assert "2 anotaciones" in memoria.clear()
    assert memoria.database.count() == 0


def test_clearing_an_empty_memory_says_so(memoria: MemoryManager) -> None:
    assert "ya estaba vacía" in memoria.clear()


# --------------------------------------------------------------------------- #
# Listado
# --------------------------------------------------------------------------- #


def test_the_summary_groups_by_category(memoria: MemoryManager) -> None:
    memoria.remember("proyecto de python", "una ruta", category="proyectos")
    memoria.remember("cumpleaños", "el 3 de marzo", category="personal")

    resumen = memoria.summary()

    assert "proyectos:" in resumen
    assert "personal:" in resumen
    assert "Recuerdo 2 cosas" in resumen


def test_an_empty_memory_says_so(memoria: MemoryManager) -> None:
    assert "nada anotado" in memoria.summary()


# --------------------------------------------------------------------------- #
# Persistencia
# --------------------------------------------------------------------------- #


def test_facts_survive_a_restart(tmp_path: Path) -> None:
    """La memoria pierde su sentido si no sobrevive al cierre del programa."""
    ruta = tmp_path / "memoria.db"

    primera = MemoryManager(database=MemoryDatabase(path=ruta))
    primera.remember("proyecto de python", "está en D:/Proyectos/JARVIS")

    segunda = MemoryManager(database=MemoryDatabase(path=ruta))
    assert segunda.database.count() == 1
    assert "JARVIS" in segunda.recall("proyecto")[0].content


def test_opening_an_existing_database_is_harmless(tmp_path: Path) -> None:
    """Las migraciones deben ser idempotentes."""
    ruta = tmp_path / "memoria.db"
    MemoryDatabase(path=ruta).save("uno", "a")
    MemoryDatabase(path=ruta)

    assert MemoryDatabase(path=ruta).count() == 1


# --------------------------------------------------------------------------- #
# Integración con el orquestador
# --------------------------------------------------------------------------- #


def test_the_orchestrator_injects_only_relevant_memory(
    memoria: MemoryManager, tmp_path: Path
) -> None:
    """Volcar la memoria entera en cada turno agotaría el presupuesto."""
    from jarvis.ai.provider import LLMResponse, TextBlock
    from jarvis.core.orchestrator import Orchestrator
    from jarvis.core.registry import ToolRegistry
    from jarvis.security.audit import AuditLog
    from jarvis.security.guard import Guard

    memoria.remember("proyecto de python", "está en D:/Proyectos/JARVIS")
    memoria.remember("cumpleaños", "el 3 de marzo")

    prompts: list[str] = []

    class Proveedor:
        def chat(self, system: str, messages: object, tools: object) -> LLMResponse:
            prompts.append(system)
            return LLMResponse(blocks=(TextBlock("Listo."),))

    orquestador = Orchestrator(
        provider=Proveedor(),
        registry=ToolRegistry(),
        guard=Guard(),
        audit=AuditLog(path=tmp_path / "audit.jsonl"),
        context_provider=memoria.context_for,
    )

    list(orquestador.submit("abre mi proyecto de python"))

    assert "D:/Proyectos/JARVIS" in prompts[0]
    assert "3 de marzo" not in prompts[0]


def test_a_failing_context_provider_does_not_break_the_turn(
    tmp_path: Path,
) -> None:
    """Perder el contexto degrada la respuesta; impedirla sería peor."""
    from jarvis.ai.provider import LLMResponse, TextBlock
    from jarvis.core.events import TurnCompleted
    from jarvis.core.orchestrator import Orchestrator
    from jarvis.core.registry import ToolRegistry
    from jarvis.security.audit import AuditLog
    from jarvis.security.guard import Guard

    def falla(texto: str) -> str:
        raise RuntimeError("la base de datos está bloqueada")

    class Proveedor:
        def chat(self, system: str, messages: object, tools: object) -> LLMResponse:
            return LLMResponse(blocks=(TextBlock("Listo."),))

    orquestador = Orchestrator(
        provider=Proveedor(),
        registry=ToolRegistry(),
        guard=Guard(),
        audit=AuditLog(path=tmp_path / "audit.jsonl"),
        context_provider=falla,
    )

    eventos = list(orquestador.submit("hola"))
    assert isinstance(eventos[-1], TurnCompleted)


def test_the_relevance_threshold_is_permissive_enough() -> None:
    """Un umbral alto dejaría la memoria inutilizada en la práctica."""
    assert 40.0 < RELEVANCE_THRESHOLD < 85.0
