"""Advisory panel choices from detached snapshots; never publish execution authority."""
import copy
from dataclasses import dataclass

from autopilot.adapters.files import FileAdapter
from autopilot.adapters.skills import SkillAdapter
from autopilot.resources import ResourceGenerations
from config_store import ConfigStore
from external_mcp.registry import ExternalMCPRegistry
from okf_engine.bundles import evaluate_bundles
from okf_engine.discovery import effective_knowledge
from roots_registry import RootsRegistry
from server.panel_timing import measure
from skills_engine.trust import TrustStore
from state import resolve_central_skills_dir


@dataclass(repr=False)
class CatalogSnapshot:
    config: dict
    skills: object
    catalogs: dict
    catalog_time: float
    knowledge_enabled: bool
    mcp_enabled: bool
    project_selection: object


def capture_catalog(runtime):
    # These locks protect RAM copies only. No filesystem checks or scanning.
    config = runtime.store.snapshot()
    registry = runtime.skills.registry
    skills = registry.scan_copy() if registry is not None else None
    tools = runtime.mcp.tools
    catalogs, timestamp = {}, 0
    if tools is not None:
        with tools.lock:
            catalogs = copy.deepcopy(tools.catalogs)
            timestamp = tools.clock()
    return CatalogSnapshot(config, skills, catalogs, timestamp,
                           runtime.knowledge.service is not None, tools is not None,
                           runtime.controller.sessions.project_selection)


def catalog_choices(snapshot, timings=None):
    """Filesystem I/O uses private adapters/stores/generations, outside runtime locks.

    Choices are hints for the editor. Preview, approval and dispatch still pin
    and validate live authority through autopilot_resources and runtime adapters.
    """
    store = ConfigStore(snapshot.config)
    roots = RootsRegistry(store)
    files = FileAdapter(roots, ResourceGenerations())
    choices = []
    with measure(timings, 'catalog_roots'):
        root_list = roots.list()
        for root in root_list:
            ref, available = files.root_ref(root)
            if available:
                choices.append(dict(key='root:' + root.alias, kind='root', name=root.alias,
                                    path=ref.path, mode=ref.mode))
    with measure(timings, 'catalog_knowledge'):
        if snapshot.knowledge_enabled:
            knowledge = effective_knowledge(snapshot.config.get('knowledge', {}), root_list)
            states = evaluate_bundles(knowledge, root_list,
                                      resolve_central_skills_dir(snapshot.config), allow_project=True)
            for state in sorted(states, key=lambda item: item.id or ''):
                if state.entry is not None and state.entry.scope == 'project':
                    sid, aliases = snapshot.project_selection
                    if sid is None or state.entry.root not in aliases:
                        continue
                if state.available:
                    choices.append(dict(key='bundle:' + state.id, kind='bundle', name=state.id))
    with measure(timings, 'catalog_scan'):
        if snapshot.skills is not None:
            snapshot.skills.rescan()
    with measure(timings, 'catalog_skills'):
        if snapshot.skills is not None:
            adapter = SkillAdapter(snapshot.skills, TrustStore(snapshot.config), store, ResourceGenerations())
            for record in snapshot.skills.list_active():
                if record.source != 'central':
                    continue
                try:
                    _, access = adapter.reference(record)
                except (ValueError, OSError):
                    continue
                choices.append(dict(key='skill:' + record.name, kind='skill', name=record.name,
                                    trusted=access.trusted, approval_required=access.approval_required))
    with measure(timings, 'catalog_profiles'):
        if snapshot.mcp_enabled:
            listing = ExternalMCPRegistry(store).list()
            if not listing['configuration_valid']:
                raise ValueError('resources_unavailable')
            for profile in listing['profiles']:
                if not profile['enabled']:
                    continue
                catalog = snapshot.catalogs.get(profile['id'])
                entries = []
                if (catalog is not None and catalog['expires'] > snapshot.catalog_time
                        and catalog['revision'] == listing['revision']):
                    entries = [dict(name=row['name'], schema_digest=row['schemaSha256'])
                               for row in catalog['tools']]
                choices.append(dict(key='profile:' + profile['alias'], kind='profile', name=profile['alias'],
                                    tools=entries))
    return choices
