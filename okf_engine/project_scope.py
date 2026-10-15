"""Jawny zakres projektowy: docs lub dwa legacy pliki; bez skanu całego repo."""
LEGACY_FILES = frozenset({".vibecoder_context.md", ".vibecoder_checkpoints.md"})


def allowed_path(state, relative: str, *, directory=False):
    entry = state.entry
    if entry.scope == "central":
        return True
    parts = relative.split("/")
    if relative.casefold() in LEGACY_FILES and not directory and entry.profile in ("legacy", "mixed"):
        return True
    if any(part.startswith(".") for part in parts) or (not directory and parts[-1].casefold() == "agents.md"):
        return False
    if any(part.casefold() in ("archive", "archives") for part in (parts if directory else parts[:-1])):
        return False
    if entry.profile == "docs":
        return parts[0].casefold() not in ("archive", "archives", ".git", ".agents", ".codex")
    if entry.profile == "mixed" and parts[0].casefold() == "docs":
        return len(parts) == 1 or parts[1].casefold() not in ("archive", "archives")
    return False
