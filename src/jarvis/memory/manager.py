"""Gestión de la memoria: qué se recuerda y qué se recupera.

Dos decisiones gobiernan este módulo, y ambas responden a restricciones
reales.

**Solo se recuerda lo que el usuario autoriza.** Una memoria que se llena sola
con todo lo que pasa por la conversación acaba siendo un archivo de cosas que
nadie pidió guardar. El asistente puede proponer recordar algo, pero quien
decide es el usuario.

**Solo se recupera lo pertinente.** Volcar la memoria entera en cada turno
parece cómodo y es inviable: el catálogo de herramientas ya consume la mayor
parte del presupuesto de tokens por minuto, y la memoria crecería sin límite
hasta desplazarlo. En su lugar se buscan los hechos relacionados con lo que el
usuario acaba de decir, y solo esos viajan.

La búsqueda reutiliza ``jarvis.computer.matching``, la misma que localiza
aplicaciones y ventanas: el problema —encontrar aquello que el usuario nombró
de forma aproximada— vuelve a ser el mismo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.computer import matching
from jarvis.memory.database import Fact, MemoryDatabase

__all__ = ["MAX_RECALLED", "MemoryManager"]

#: Hechos que se inyectan como contexto en un turno. Un tope bajo es
#: deliberado: cada hecho recuperado resta espacio al resto de la conversación.
MAX_RECALLED = 5

#: Puntuación mínima para considerar que un hecho viene al caso.
#:
#: El valor se fijó midiendo, no por intuición. Con órdenes reales, los
#: aciertos puntúan entre 88 y 96 y los falsos entre 33 y 37: cualquier valor
#: intermedio separa ambos grupos, y 60 deja margen por los dos lados.
RELEVANCE_THRESHOLD = 60.0


@dataclass(slots=True)
class MemoryManager:
    """API de alto nivel de la memoria del asistente."""

    database: MemoryDatabase = field(default_factory=MemoryDatabase)
    enabled: bool = True
    """Permite desactivar la memoria sin borrarla."""

    # -- Escritura ---------------------------------------------------------- #

    def remember(self, subject: str, content: str, category: str = "general") -> str:
        """Guarda un hecho.

        Args:
            subject: Aquello sobre lo que trata.
            content: Lo que hay que recordar.
            category: Agrupación temática.

        Returns:
            Una confirmación redactada para el usuario.
        """
        if not self.enabled:
            return "La memoria está desactivada."

        asunto = " ".join(subject.split()).strip()
        cuerpo = content.strip()

        if not asunto or not cuerpo:
            return "Para recordar algo necesito saber sobre qué y qué exactamente."

        nuevo = self.database.save(asunto, cuerpo, category)
        return (
            f"Anotado: {asunto}." if nuevo else f"Actualizado lo que sabía de {asunto}."
        )

    def forget(self, subject: str) -> str:
        """Olvida un hecho.

        Se admite un nombre aproximado: el usuario no recuerda con qué palabras
        exactas se guardó.

        Args:
            subject: Aquello que debe olvidarse.

        Returns:
            Una confirmación redactada para el usuario.
        """
        if self.database.delete(subject.strip()):
            return f"Olvidado: {subject}."

        hechos = self.database.all_facts()
        try:
            hecho = matching.resolve(
                subject, hechos, lambda f: (f.subject,), what="anotación"
            )
        except matching.AmbiguousMatchError as exc:
            return (
                f"«{subject}» coincide con varias anotaciones: "
                f"{', '.join(exc.names)}. Pregunta al usuario cuál olvidar."
            )
        except matching.NoMatchError:
            return f"No tengo nada anotado sobre «{subject}»."

        self.database.delete(hecho.subject)
        return f"Olvidado: {hecho.subject}."

    def clear(self) -> str:
        """Vacía la memoria por completo."""
        borrados = self.database.clear()
        if not borrados:
            return "La memoria ya estaba vacía."
        return f"Borrada toda la memoria: {borrados} anotaciones."

    # -- Lectura ------------------------------------------------------------ #

    def recall(self, query: str, limit: int = MAX_RECALLED) -> list[Fact]:
        """Busca los hechos relacionados con un texto.

        Args:
            query: Texto sobre el que buscar, normalmente la orden del usuario.
            limit: Número máximo de hechos.

        Returns:
            Los hechos pertinentes, de más a menos.
        """
        if not self.enabled or not query.strip():
            return []

        coincidencias = matching.rank(
            query, self.database.all_facts(), lambda f: f.search_terms
        )
        return [
            m.item  # type: ignore[misc]
            for m in coincidencias
            if m.score >= RELEVANCE_THRESHOLD
        ][:limit]

    def context_for(self, text: str) -> str:
        """Redacta el contexto de memoria que acompaña a un turno.

        Args:
            text: Lo que el usuario acaba de decir.

        Returns:
            Las anotaciones pertinentes, o una cadena vacía si no hay ninguna.
            El orquestador añade este texto a las instrucciones de sistema.
        """
        pertinentes = self.recall(text)
        if not pertinentes:
            return ""

        lineas = "\n".join(f"- {f.describe()}" for f in pertinentes)
        return f"Lo que sabes del usuario y viene al caso ahora:\n{lineas}"

    def summary(self) -> str:
        """Enumera lo que el asistente recuerda.

        Returns:
            Un listado agrupado por categoría, redactado para el usuario.
        """
        hechos = self.database.all_facts()
        if not hechos:
            return "No tengo nada anotado todavía."

        por_categoria: dict[str, list[Fact]] = {}
        for hecho in hechos:
            por_categoria.setdefault(hecho.category, []).append(hecho)

        bloques = []
        for categoria in sorted(por_categoria):
            lineas = "\n".join(f"  · {f.describe()}" for f in por_categoria[categoria])
            bloques.append(f"{categoria}:\n{lineas}")

        return f"Recuerdo {len(hechos)} cosas:\n\n" + "\n\n".join(bloques)
