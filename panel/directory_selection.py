"""Convert a local dialog result to an existing, link-free directory inside a root."""
from pathlib import Path

from okf_engine.paths import PathSecurityError, check_directory_chain, locate_bundle_dir


def relative_directory(root_path, selected):
    if selected is None:
        return None
    try:
        base, target = Path(root_path), Path(selected)
        check_directory_chain(base)
        check_directory_chain(target)
        resolved_base, resolved_target = base.resolve(), target.resolve()
        relative = resolved_target.relative_to(resolved_base)
        subpath = "" if relative == Path(".") else relative.as_posix()
        # Use the same segment and filesystem rules as knowledge bundles.
        if locate_bundle_dir(str(base), subpath) != resolved_target:
            raise ValueError("Directory changed.")
        return subpath
    except (OSError, RuntimeError, ValueError, PathSecurityError):
        raise ValueError("Wybierz istniejący katalog wewnątrz wskazanego roota, bez linków i junctionów.") from None
