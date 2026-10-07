"""Tipos de domínio devolvidos pela camada de armazenamento."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float


@dataclass(frozen=True)
class Reserva:
    codigo: str
    area: str
    data: str

    def para_dict(self) -> dict[str, str]:
        return {"codigo": self.codigo, "area": self.area, "data": self.data}


@dataclass(frozen=True)
class Visitante:
    nome: str
    data: str

    def para_dict(self) -> dict[str, str]:
        return {"nome": self.nome, "data": self.data}


@dataclass(frozen=True)
class ResultadoReserva:
    status: Literal["criada", "ocupada", "area_invalida", "data_invalida", "erro"]
    codigo: str | None = None
    mensagem: str | None = None


@dataclass(frozen=True)
class ResultadoVisitante:
    status: Literal["autorizado", "ja_autorizado", "data_invalida", "nome_invalido", "erro"]
    mensagem: str | None = None


@dataclass(frozen=True)
class ResumoRestauracao:
    apartamentos: int
    areas: int
    reservas: int
    visitantes: int

    def __str__(self) -> str:
        return (
            f"Restaurado: {self.apartamentos} apartamentos, {self.areas} áreas, "
            f"{self.reservas} reservas, {self.visitantes} visitantes."
        )
