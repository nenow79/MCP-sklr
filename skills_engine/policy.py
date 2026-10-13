#!/usr/bin/env python3
"""Polityka potwierdzeń uruchamiania skryptów skills (czysta funkcja, bez I/O)."""
from dataclasses import dataclass
from typing import Mapping

from skills_engine.models import SOURCE_CENTRAL, SOURCE_DEV

CONFIRM_KEY = "vibecoder-confirm"


@dataclass(frozen=True)
class ConfirmDecision:
    required: bool      # czy pytać użytkownika
    offer_trust: bool   # czy w oknie pokazać opcję "Ufaj i uruchom"
    reason: str


def declares_no_confirm(metadata: Mapping[str, str]) -> bool:
    """Skill deklaruje brak potwierdzenia: metadata.vibecoder-confirm == false (bez względu na wielkość liter)."""
    return str(metadata.get(CONFIRM_KEY, "")).strip().lower() == "false"


def decide_confirmation(source: str, metadata: Mapping[str, str], trusted: bool) -> ConfirmDecision:
    if source != SOURCE_CENTRAL:
        label = "Skill deweloperski" if source == SOURCE_DEV else "Skill projektowy"
        return ConfirmDecision(
            True, False,
            f"{label}: potwierdzenie wymagane zawsze (flaga vibecoder-confirm jest ignorowana).",
        )
    if not declares_no_confirm(metadata):
        return ConfirmDecision(True, False, "Domyślnie każde uruchomienie skryptu wymaga potwierdzenia.")
    if trusted:
        return ConfirmDecision(False, False, "Skill centralny, zaufany (hash zgodny), vibecoder-confirm=false.")
    return ConfirmDecision(
        True, True,
        "Skill deklaruje vibecoder-confirm=false, ale nie jest zaufany (nowy lub zmieniony od ostatniego zaufania).",
    )
