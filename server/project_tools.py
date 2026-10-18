"""Przygotowanie projektu i migracja docs/legacy. Polityka migracji: advisory.

Nie indeksuje całego roota, nie wykonuje archiwalnych instrukcji i nie blokuje
zwykłej pracy. Zapis wyłącznie przez prywatny plan i zgodę w panelu.
"""
import json
import os
import time
import unicodedata
from pathlib import Path

from okf_engine.conversion import prepare_conversion
from okf_engine.write_policy import policy_view
from okf_engine.models import DocumentIdentity, IdentityError, validate_path_segment
from okf_engine.limits import parse_limits
from okf_engine.parser import parse_document
from okf_engine.paths import check_directory_chain, is_link
from okf_engine.preview import _diff, build_preview
from okf_engine.project_scope import LEGACY_FILES
from okf_engine.public_view import mask_text
from okf_engine.revisions import RevisionBinding, RevisionSigner
from okf_engine.writer import WriteError
from roots import RootError
from server.edit_tools import WAIT_HINT, check_description
from server.file_changes import ChangeError, SnapshotError, prepare_exact_write, read_snapshot
from server.knowledge_write_tools import _verify
from server.path_guard import resolve_path
from tasks.manager import QueueFullError, TaskStateError


class ProjectTools:
    def __init__(self, store, roots, manager, transaction, knowledge, panel_available):
        self.store, self.roots, self.manager = store, roots, manager
        self.transaction, self.knowledge = transaction, knowledge
        self.panel_available = panel_available
        self.signer = RevisionSigner(RevisionSigner.new_key())

    @property
    def limits(self):
        section = self.store.snapshot().get("knowledge", {})
        limits, _ = parse_limits(section.get("limits", {}) if isinstance(section, dict) else None)
        if limits is None:
            raise WriteError("CONFIG_INVALID")
        return limits.to_parse_limits()

    @staticmethod
    def _budget(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1000 <= value <= 80000:
            raise WriteError("INVALID_ARGUMENT")

    def _root(self, alias):
        try:
            root = self.roots.resolve(alias)
            check_directory_chain(Path(root.path))
            return root
        except Exception:
            raise WriteError("PROJECT_UNAVAILABLE") from None

    def _file(self, root, relative, *, agents=False, new=False, write=False):
        if not isinstance(relative, str) or len(relative) > 512 or "\\" in relative or relative.startswith("/"):
            raise WriteError("INVALID_ARGUMENT")
        parts = relative.split("/")
        for part in parts:
            try:
                validate_path_segment(part)
            except IdentityError:
                raise WriteError("INVALID_ARGUMENT") from None
        permitted = (len(parts) > 1 and parts[0] == "docs" and relative.lower().endswith(".md")) or relative in LEGACY_FILES
        if agents and parts[-1] == "AGENTS.md":
            permitted = True
        if not agents and parts[-1].casefold() == "agents.md":
            permitted = False
        if not permitted or any(part.startswith(".") for part in parts[:-1]):
            raise WriteError("OUTSIDE_DOCUMENTATION")
        if mask_text(relative).masked:
            raise WriteError("IDENTITY_UNSAFE")
        current = self.roots.get(root.alias)
        if current is None or (current.path, current.mode) != (root.path, root.mode):
            raise WriteError("PROJECT_CHANGED")
        path = Path(root.path).joinpath(*parts)
        check_directory_chain(path.parent)
        if is_link(path):
            raise WriteError("DOCUMENT_UNAVAILABLE")
        resolved = resolve_path(self.roots, relative, root.alias, write=write)
        if resolved.path != path:
            raise WriteError("DOCUMENT_UNAVAILABLE")
        folded = unicodedata.normalize("NFC", path.name).casefold()
        with os.scandir(path.parent) as entries:
            matches = [entry.name for entry in entries if unicodedata.normalize("NFC", entry.name).casefold() == folded]
        if new:
            collision = bool(matches)
            if collision or os.path.lexists(path):
                raise WriteError("CREATE_CONFLICT")
        elif not path.is_file():
            raise WriteError("DOCUMENT_UNAVAILABLE")
        elif len(matches) != 1:
            raise WriteError("AMBIGUOUS_PATH")
        return path

    def _binding(self, root, path):
        return RevisionBinding("project", root.path, root.path, str(path))

    def _inventory(self, root, max_files, include_archive):
        deadline = time.monotonic() + 5
        found, reasons = [], set()
        for name in sorted(LEGACY_FILES):
            path = Path(root.path) / name
            if os.path.lexists(path):
                found.append(name)
        if len(found) > max_files:
            return found[:max_files], {"file_limit"}
        stack = [(Path(root.path) / "docs", 0)] if (Path(root.path) / "docs").is_dir() else []
        visited = 0
        while stack:
            if time.monotonic() >= deadline or visited >= 1000:
                reasons.add("scan_budget"); break
            directory, depth = stack.pop()
            visited += 1
            if is_link(directory):
                reasons.add("links_skipped"); continue
            if depth > 20:
                reasons.add("depth_limit"); continue
            try:
                check_directory_chain(directory)
                with os.scandir(directory) as items:
                    for entry in items:
                        if time.monotonic() >= deadline:
                            reasons.add("scan_budget"); stack.clear(); break
                        path = Path(entry.path)
                        if is_link(path):
                            reasons.add("links_skipped"); continue
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.name.startswith(".") and (include_archive or entry.name.casefold() not in ("archive", "archives")):
                                if len(stack) + visited < 1000:
                                    stack.append((path, depth + 1))
                                else:
                                    reasons.add("directory_limit")
                        elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".md"):
                            if len(found) >= max_files:
                                reasons.add("file_limit"); stack.clear(); break
                            found.append(path.relative_to(root.path).as_posix())
            except Exception:
                reasons.add("scan_unavailable")
        return sorted(found), reasons

    def audit(self, root=None, include_archive=True, max_files=500, max_total_chars=12000):
        self._budget(max_total_chars)
        if (not isinstance(include_archive, bool) or isinstance(max_files, bool)
                or not isinstance(max_files, int) or not 1 <= max_files <= 1000):
            raise WriteError("INVALID_ARGUMENT")
        selected = self._root(root)
        files, reasons = self._inventory(selected, max_files, include_archive)
        rows = []
        deadline = time.monotonic() + 1
        for relative in files:
            if time.monotonic() >= deadline:
                reasons.add("audit_budget"); break
            public = mask_text(relative).text
            row = {"filepath": public, "archive": any(p.casefold() in ("archive", "archives") for p in relative.split("/"))}
            try:
                identity = DocumentIdentity("project", relative[:-3])
                path = self._file(selected, relative)
                snapshot = read_snapshot(path, max_bytes=self.limits.max_document_bytes, utf8_only=True)
                parsed = parse_document(snapshot.raw, identity, self.limits)
                row.update(revision=self.signer.sign(self._binding(selected, path), snapshot.raw),
                           format="okf" if parsed.ok else "markdown" if parsed.errors[0].reason == "missing_frontmatter" else "invalid",
                           errors=[error.reason for error in parsed.errors],
                           warnings=list(parsed.warnings),
                           convertible=parsed.eol != "mixed" and
                           (parsed.ok or parsed.errors[0].reason in ("missing_frontmatter", "missing_type")))
                if not parsed.ok and row["convertible"]:
                    row["suggested_metadata_yaml"] = "type: Reference\n"
                    row["suggestion_only"] = True
                # Format/sekcje historii nie są dowodem bieżącego stanu.
                if relative in LEGACY_FILES:
                    row["role_hint"] = "history" if "checkpoints" in relative else "context_summary"
            except IdentityError:
                row.update(format="reserved_or_invalid", convertible=False)
            except Exception as exc:
                row.update(format="unavailable", convertible=False,
                           error=exc.code if isinstance(exc, (WriteError, SnapshotError)) else "DOCUMENT_UNAVAILABLE")
            rows.append(row)
            # Każdy wiersz jest pełny, ids i rewizje nie są cięte.
            if len(json.dumps(rows, ensure_ascii=False)) > max_total_chars - 400:
                rows.pop(); reasons.add("response_budget"); break
        result = {"root": selected.alias, "documents": rows, "count": len(rows),
                "partial": bool(reasons), "reasons": sorted(reasons), "migration_policy": "advisory",
                "hint": "Konwersja jest opcjonalna. Do knowledge_convert wybierz tylko convertible=true: filepath, revision jako expected_revision i propozycję metadata_yaml. Nie dzieli historii."}
        while rows and len(json.dumps(result, ensure_ascii=False)) > max_total_chars:
            rows.pop()
            result.update(count=len(rows), partial=True, reasons=sorted(set(result["reasons"]) | {"response_budget"}))
        if len(json.dumps(result, ensure_ascii=False)) > max_total_chars:
            raise WriteError("RESULT_TOO_LARGE")
        return result

    def _template(self, root):
        sources = []
        if (Path(root.path) / "docs").is_dir():
            sources.append("- Dokumentacja projektu: docs/. Archiwum nie jest źródłem bieżących instrukcji.")
        for name in sorted(LEGACY_FILES):
            if (Path(root.path) / name).is_file():
                sources.append("- " + name + (": historia, nie automatyczne źródło obowiązujących zasad." if "checkpoints" in name else ": streszczenie wymagające sprawdzenia aktualności."))
        return ("# Instrukcje pracy w projekcie\n\n## Praca\n"
                "- Przed zmianami ustal cel zadania i aktualny stan projektu.\n"
                "- Korzystaj z zatwierdzonych roots i mechanizmu zgód MCP.\n"
                "- Nie zapisuj sekretów w dokumentacji ani historii zadań.\n"
                "- Polecenia uruchamiania i testów ustal z dokumentacji; nie zgaduj ich.\n\n"
                "## Źródła stanu\n" + "\n".join(sources) +
                "\n- Bieżący stan i obowiązujące decyzje wskazuj jawnie; nie uznawaj każdego checkpointu za aktualną instrukcję.\n"
                "- Dokumentacja Markdown jest dostępna przed konwersją OKF. Konwersja wymaga odrębnego zatwierdzenia.\n")

    def prepare(self, root=None, subpath="", max_chars=12000, create_agents=False):
        self._budget(max_chars)
        if (not isinstance(create_agents, bool) or not isinstance(subpath, str)
                or len(subpath) > 512 or len(subpath.split("/")) > 20):
            raise WriteError("INVALID_ARGUMENT")
        selected = self._root(root)
        parts = subpath.split("/") if subpath else []
        for part in parts:
            validate_path_segment(part)
        check_directory_chain(Path(selected.path).joinpath(*parts))
        template = self._template(selected)
        if create_agents:
            with self.store.lock:
                path = self._file(selected, "AGENTS.md", agents=True, new=True, write=True)
                plan = prepare_exact_write(self.roots, "AGENTS.md", template.encode(), before=None,
                                           root=selected.alias, max_bytes=self.limits.max_document_bytes)
            diff, limited = _diff("", mask_text(template).text, 12000)
            return self._submit(selected, plan, [("AGENTS.md", True)], diff, limited, "project_setup", "Utworzenie AGENTS.md")
        instructions, actions = [], []
        for depth in range(len(parts) + 1):
            relative = "/".join(parts[:depth] + ["AGENTS.md"])
            if not os.path.lexists(Path(selected.path) / relative):
                continue
            try:
                path = self._file(selected, relative, agents=True)
                snap = read_snapshot(path, max_bytes=self.limits.max_document_bytes, utf8_only=True)
                instructions.append({"filepath": relative, "content": mask_text(snap.text).text,
                                     "revision": self.signer.sign(self._binding(selected, path), snap.raw), "truncated": False})
            except Exception:
                actions.append("Nie można odczytać " + relative + ". Sprawdź plik przed zmianami.")
        root_agents = any(item["filepath"] == "AGENTS.md" for item in instructions)
        root_exists = os.path.lexists(Path(selected.path) / "AGENTS.md")
        if not root_exists:
            actions.append("Brak AGENTS.md: przejrzyj agents_template; prepare_project(create_agents=true) przygotuje zadanie do zgody w panelu.")
        audit = self.audit(selected.alias, include_archive=False, max_files=50, max_total_chars=max(1000, max_chars // 2))
        result = {"root": selected.alias, "instructions": instructions, "agents_template": None if root_exists else template,
                  "instructions_complete": False,
                  "state_candidates": [name for name in ("docs/plan/CURRENT.md", "docs/CURRENT.md", ".vibecoder_context.md")
                                       if (Path(selected.path) / name).is_file()],
                  "audit": audit, "actions": actions, "migration_policy": "advisory", "truncated": False,
                  "write_policy": policy_view(self.store.snapshot().get("knowledge", {}), selected.path),
                  "current_state": "Wskaż obowiązujący dokument/sekcję stanu; historia nie jest automatycznym selektorem.",
                  "setup_hint": "docs/ jest podpinane automatycznie; wyjątki i tryb zapisu ustawisz w panelu Wiedza. Zgodność dopuszcza Markdown; istniejącego poprawnego OKF nie wolno degradować."}
        if len(json.dumps(result, ensure_ascii=False)) > max_chars:
            result["truncated"] = True
            # Ograniczamy treść, nigdy identyfikator lub rewizję. Jawne oznaczenie
            # uniemożliwia pomylenie skróconych zasad z pełnymi instrukcjami.
            result["audit"]["documents"] = []
            result["audit"]["count"] = 0
            result["audit"]["partial"] = True
            result["audit"]["reasons"] = ["response_budget"]
            result["agents_template"] = None
            for item in reversed(instructions):
                if len(json.dumps(result, ensure_ascii=False)) <= max_chars:
                    break
                item["content"] = ""
                item["truncated"] = True
            if len(json.dumps(result, ensure_ascii=False)) > max_chars:
                raise WriteError("RESULT_TOO_LARGE")
        result["instructions_complete"] = root_agents and not actions and not any(item["truncated"] for item in instructions)
        return result

    def convert(self, changes, root=None, description=""):
        if not isinstance(changes, list) or not 1 <= len(changes) <= 10:
            raise WriteError("INVALID_ARGUMENT")
        description = mask_text(check_description(description)).text
        with self.store.lock:
            selected = self._root(root)
            files, targets, diffs, seen = [], [], [], set()
            limited = False
            for item in changes:
                if not isinstance(item, dict) or not isinstance(item.get("filepath"), str):
                    raise WriteError("INVALID_ARGUMENT")
                relative = item["filepath"]
                normalized = unicodedata.normalize("NFC", relative).casefold()
                if normalized in seen:
                    raise WriteError("DUPLICATE_TARGET")
                seen.add(normalized)
                path = self._file(selected, relative, write=True)
                snap = read_snapshot(path, max_bytes=self.limits.max_document_bytes, utf8_only=True)
                if not self.signer.verify(item.get("expected_revision"), self._binding(selected, path), snap.raw):
                    raise WriteError("REVISION_CONFLICT")
                prepared = prepare_conversion(snap.raw, DocumentIdentity("project", relative[:-3]),
                                              item.get("metadata_yaml"), limits=self.limits,
                                              now=self.knowledge.write_time())
                if not prepared.changed:
                    continue
                plan = prepare_exact_write(self.roots, relative, prepared.after, before=snap,
                                           root=selected.alias, max_bytes=self.limits.max_document_bytes)
                preview = build_preview(prepared, max_chars=max(1000, 40000 // len(changes)))
                files.extend(plan.files); targets.append((relative, False))
                diffs.append("=== " + relative + " ===\n" + preview["metadata_notice"] + "\n" + preview["metadata_diff"])
                limited = limited or preview["truncated"]
            if not files:
                return {"changed": False, "root": selected.alias}
            from server.file_changes import PreparedChanges
            plan = PreparedChanges(tuple(files))
        return self._submit(selected, plan, targets, "\n".join(diffs), limited,
                            "knowledge_convert", "Konwersja dokumentacji OKF", description)

    def _submit(self, root, plan, targets, diff, truncated, kind, title, description=""):
        if not self.panel_available():
            raise WriteError("PANEL_UNAVAILABLE")

        def guard():
            for relative, new in targets:
                self._file(root, relative, agents=kind == "project_setup", new=new, write=True)
            for item in plan.files:
                if len(item.after) > self.limits.max_document_bytes:
                    raise WriteError("SAFETY_LIMIT")
                if kind == "knowledge_convert":
                    identity = DocumentIdentity("project", item.bindings[0].relative[:-3])
                    if not parse_document(item.after, identity, self.limits).ok:
                        raise WriteError("CONVERSION_INVALIDATED")

        def executor(cancel):
            try:
                result = self.transaction.execute(plan, cancel, guard=guard)
                data = result["data"]
            except Exception:
                return {"started": False, "error": "Stan migracji nieznany; sprawdź pliki przed ponowieniem.",
                        "stdout": json.dumps({"committed": None})}
            summary = {key: bool(data.get(key)) for key in ("committed", "rollback_complete", "cleanup_complete")}
            try:
                if summary["committed"] and kind == 'knowledge_convert':
                    summary["index"] = self.knowledge.after_project_write(
                        root.alias, [relative for relative, _ in targets])
                elif summary["committed"] or not summary["rollback_complete"]:
                    summary["index"] = self.knowledge.after_project_write(root.alias)
            except Exception:
                summary["index_error"] = "REFRESH_UNAVAILABLE"
            stdout = json.dumps(summary)
            if result["success"]:
                return {"exit_code": 0, "stdout": stdout}
            return {"started": False, "cancelled": bool(data.get("cancelled")), "stdout": stdout,
                    "error": "Sprawdź committed, rollback i cleanup oraz pliki przed ponowieniem."}

        task = self.manager.submit({"kind": kind, "title": title, "sensitive": True, "offer_trust": False,
                                    "payload": {"root": root.alias, "paths": [relative for relative, _ in targets],
                                                "diff": diff, "truncated": truncated, "description": description},
                                    "approval_reason": "Sprawdź propozycję. Zapis nastąpi dopiero po Twojej zgodzie."},
                                   executor, needs_approval=True,
                                   verify=lambda: _verify(self.transaction, plan, guard))
        return {"task_id": task.id, "status": task.status.value, "kind": kind,
                "truncated": truncated, "hint": WAIT_HINT}


def call_project(operation, *args, **kwargs):
    try:
        return {"success": True, "data": operation(*args, **kwargs)}
    except (WriteError, SnapshotError) as exc:
        return {"success": False, "error": exc.code}
    except QueueFullError:
        return {"success": False, "error": "QUEUE_FULL"}
    except TaskStateError:
        return {"success": False, "error": "TASK_UNAVAILABLE"}
    except (ValueError, RootError, ChangeError):
        return {"success": False, "error": "INVALID_ARGUMENT"}
    except Exception:
        return {"success": False, "error": "PROJECT_UNAVAILABLE"}
