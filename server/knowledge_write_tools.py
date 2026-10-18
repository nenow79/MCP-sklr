"""C3b: prywatny plan OKF -> publiczny podgląd -> zgoda -> FileTransaction.

W domknięciach żyją snapshot, bajty i klucz rewizji. Nigdy PreparedChanges.preview
(maskowanie ogólnych .md nie wystarcza), asdict planu ani surowy wyjątek do kolejki.
"""
import json

from okf_engine.documents import DocumentError
from okf_engine.models import DocumentIdentity, IdentityError
from okf_engine.paths import PathSecurityError, resolve_concept_file, resolve_new_concept_file
from okf_engine.preview import build_preview
from okf_engine.project_scope import allowed_path
from okf_engine.public_view import mask_text
from okf_engine.revisions import RevisionBinding
from okf_engine.service import KnowledgeError
from okf_engine.writer import WriteError, prepare_write
from server.edit_tools import WAIT_HINT, check_description
from server.file_changes import ChangeError, SnapshotError, prepare_exact_write, read_snapshot
from server.path_guard import PathError, resolve_path
from tasks.manager import QueueFullError, TaskStateError


def _target(context, concept_id, mode):
    resolver = resolve_new_concept_file if mode == "create" else resolve_concept_file
    try:
        path, canonical = resolver(context.bundle.path, concept_id)
        if not allowed_path(context.bundle, path.relative_to(context.bundle.path).as_posix()):
            raise KnowledgeError("DOCUMENT_UNAVAILABLE")
        return path, canonical
    except PathSecurityError as exc:
        if mode == "create" and exc.code in ("already_exists", "ambiguous"):
            raise KnowledgeError("CREATE_CONFLICT") from None
        raise KnowledgeError("DOCUMENT_UNAVAILABLE") from None


def _diff(preview):
    flags = [preview["metadata_notice"]]
    if preview["source_format_changed"]:
        flags.append("Zmieniono formatowanie lub komentarze źródła.")
    if preview["eol_adjusted"]:
        flags.append("Końce linii propozycji dostosowano do formatu dokumentu.")
    if preview["masked"]:
        flags.append("Podgląd zawiera zamaskowane wartości.")
    if preview["truncated"]:
        flags.append("Podgląd częściowy: " + ", ".join(preview["reasons"]))
    if preview["warnings"]:
        flags.append("Ostrzeżenia: " + ", ".join(preview["warnings"]))
    flags.extend(["\nMetadane:\n" + preview["metadata_diff"],
                  "\nTreść:\n" + preview["body_diff"]])
    return "\n".join(flags)


def _write_path(roots, relative, context):
    try:
        return resolve_path(roots, relative, context.bundle.entry.root, write=True)
    except PathError:
        raise KnowledgeError("WRITE_FORBIDDEN") from None


def _invalidate(service, bundle_id):
    try:
        service.index.invalidate(bundle_id)
    except Exception:
        pass  # błędów porządkowania cache nie przekazujemy loggerowi kolejki


def _verify(transaction, plan, guard):
    try:
        return transaction.verify(plan, guard=guard)
    except Exception:
        return "Zapis OKF przerwany: nie udało się ponownie sprawdzić planu."


def _executor(service, transaction, plan, context, guard, concept_id):
    def execute(cancel_event):
        try:
            result = transaction.execute(plan, cancel_event, guard=guard)
            data = result["data"]
        except Exception:
            # Nie zakładamy braku zapisu, jeśli zawiódł nieoczekiwanie sam adapter.
            _invalidate(service, context.bundle.id)
            return {"started": False, "error": "Stan zapisu OKF nieznany; sprawdź plik przed ponowieniem.",
                    "stdout": json.dumps({"committed": None, "rollback_complete": None,
                                          "cleanup_complete": None, "index_state": "empty"})}
        try:
            summary = {"committed": bool(data["committed"]),
                       "rollback_complete": bool(data["rollback_complete"]),
                       "cleanup_complete": bool(data["cleanup_complete"]),
                       "index_state": service.index.state(context.bundle.id).state}
            if summary["committed"]:
                # Błąd odświeżenia nie zmienia powodzenia transakcji.
                summary.update(service.after_write(context.bundle.id, [concept_id]))
            elif not summary["rollback_complete"]:
                _invalidate(service, context.bundle.id)
                summary["index_state"] = "empty"
            stdout = json.dumps(summary, ensure_ascii=False)
            if not summary["rollback_complete"] or not summary["cleanup_complete"]:
                return {"started": False, "stdout": stdout,
                        "error": "Niepełny rollback lub cleanup OKF; sprawdź committed i plik przed ponowieniem."}
            if result["success"]:
                return {"exit_code": 0, "stdout": stdout}
            if data.get("cancelled"):
                return {"cancelled": True, "stdout": stdout}
            return {"started": False, "stdout": stdout,
                    "error": "Zapis OKF przerwany: konflikt lub błąd operacji plikowej."}
        except Exception:
            # Committed jest już znane. Nigdy nie przekształcamy awarii indeksu
            # lub podsumowania w zachętę do ponownego create/update.
            _invalidate(service, context.bundle.id)
            summary = {"committed": bool(data.get("committed")),
                       "rollback_complete": bool(data.get("rollback_complete")),
                       "cleanup_complete": bool(data.get("cleanup_complete")),
                       "index_state": "empty", "index_error": "REFRESH_UNAVAILABLE"}
            if result.get("success"):
                return {"exit_code": 0, "stdout": json.dumps(summary)}
            return {"started": False, "stdout": json.dumps(summary),
                    "error": "Sprawdź committed i plik przed ponowieniem zapisu OKF."}
    return execute


def knowledge_save_impl(service, roots, manager, transaction, config_lock, panel_available,
                        bundle_id, concept_id, mode, body, metadata_yaml=None,
                        expected_revision=None, description=""):
    """Natychmiastowy task_id; uprawnienia nie wynikają z włączenia bundle."""
    try:
        identity = DocumentIdentity(bundle_id, concept_id)
        if mode not in ("create", "update"):
            raise KnowledgeError("INVALID_ARGUMENT")
        if ((mode == "create" and expected_revision is not None) or
                (mode == "update" and (not isinstance(expected_revision, str) or
                                        not expected_revision or len(expected_revision) > 200))):
            raise KnowledgeError("INVALID_ARGUMENT")
        try:
            description = mask_text(check_description(description)).text
        except ChangeError:
            raise KnowledgeError("INVALID_ARGUMENT") from None
        with config_lock:
            context = service.write_context(bundle_id)
            path, canonical = _target(context, concept_id, mode)
            identity = DocumentIdentity(bundle_id, canonical)
            relative = path.relative_to(context.bundle.entry.root_path).as_posix()
            resolved = _write_path(roots, relative, context)
            if resolved.path != path:
                raise KnowledgeError("DOCUMENT_UNAVAILABLE")
            snapshot = (read_snapshot(path, max_bytes=context.limits.max_document_bytes, utf8_only=True)
                        if mode == "update" else None)
            binding = RevisionBinding(bundle_id, context.bundle.entry.root_path,
                                      str(context.bundle.path), str(path))
            if mode == "update" and not context.signer.verify(expected_revision, binding, snapshot.raw):
                raise KnowledgeError("REVISION_CONFLICT")
            prepared = prepare_write(identity, mode, body, metadata_yaml,
                                     before=snapshot.raw if snapshot is not None else None,
                                     limits=context.limits.to_parse_limits(), now=context.now)
            plan = prepare_exact_write(roots, relative, prepared.after, before=snapshot,
                                       root=context.bundle.entry.root,
                                       max_bytes=context.limits.max_document_bytes)

            def guard():
                service.check_write_context(context)
                current_path, current_id = _target(context, canonical, mode)
                if current_path != path or current_id != canonical:
                    raise KnowledgeError("DOCUMENT_UNAVAILABLE")
                current = _write_path(roots, relative, context)
                if current.path != path:
                    raise KnowledgeError("DOCUMENT_UNAVAILABLE")

            guard()
            if not prepared.changed:
                return {"success": True, "data": {"changed": False, "bundle_id": bundle_id,
                                                   "concept_id": canonical}}
            preview = build_preview(prepared)
        if not panel_available():
            raise KnowledgeError("PANEL_UNAVAILABLE")
        payload = {"bundle_id": bundle_id, "concept_id": canonical, "save_mode": mode,
                   "paths": [bundle_id + ":" + canonical], "description": description,
                   "diff": _diff(preview), "warnings": preview["warnings"],
                   "truncated": preview["truncated"], "masked": preview["masked"]}
        task = manager.submit(
            {"kind": "knowledge_write", "title": "Zapis konceptu OKF", "payload": payload,
             "sensitive": True, "offer_trust": False,
             "approval_reason": "Zapis OKF wymaga decyzji użytkownika. Sprawdź maskowany diff i ostrzeżenia."},
            _executor(service, transaction, plan, context, guard, canonical), needs_approval=True,
            verify=lambda: _verify(transaction, plan, guard))
        task = manager.get(task.id) or task
        return {"success": True, "data": {"task_id": task.id, "status": task.status.value,
                                           "kind": "knowledge_write", "bundle_id": bundle_id,
                                           "concept_id": canonical, "sensitive": True,
                                           "truncated": preview["truncated"], "hint": WAIT_HINT}}
    except (KnowledgeError, DocumentError, WriteError, SnapshotError) as exc:
        return {"success": False, "error": exc.code}
    except (IdentityError, PathError):
        return {"success": False, "error": "INVALID_ARGUMENT"}
    except ChangeError:
        return {"success": False, "error": "DOCUMENT_UNAVAILABLE"}
    except QueueFullError:
        return {"success": False, "error": "QUEUE_FULL"}
    except TaskStateError:
        return {"success": False, "error": "TASK_UNAVAILABLE"}
    except Exception:
        return {"success": False, "error": "KNOWLEDGE_INTERNAL_ERROR"}
