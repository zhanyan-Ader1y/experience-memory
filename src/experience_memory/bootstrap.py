"""Sole composition root shared by tools, native hooks and doctor."""
from dataclasses import dataclass
from pathlib import Path
from .application.services import MemoryApplicationService,RetentionApplicationService
from .application.session_binding import SessionBinding
from .config import Config
from .domain.errors import MemoryError,ErrorCode
from .infrastructure.core_loader import FileCoreLoader
from .infrastructure.session_runtime import LocalSessionRuntime
from .infrastructure.session_pointer import SessionPointerStore
from .infrastructure.mem0_client import Mem0Client,credentials,deadline
from .infrastructure.mem0_store import Mem0Store
from .infrastructure.experience_repository import Mem0ExperienceRepository
from .infrastructure.control_repository import Mem0ControlRepository
from .infrastructure.unconfigured import UnconfiguredExperienceRepository,UnconfiguredControlStateRepository
from .infrastructure.host_context import current_host


@dataclass(frozen=True)
class Runtime:
    memory: MemoryApplicationService
    retention: RetentionApplicationService
    experiences: object
    controls: object
    sessions: object
    adapter_status: tuple
    core_loader: object
    binding: object = None


def build_runtime(config:Config,*,experiences=None,controls=None,sessions=None,data_root=None,core_loader=None,
                  client=None,host_source=current_host,project_root=None,plugin_root=None):
    sessions=LocalSessionRuntime(SessionPointerStore(data_root) if data_root is not None else None,
        project_scope=config.project_scope,namespace_mapping=config.namespace_mapping,project_root=project_root) if sessions is None else sessions
    core_loader=FileCoreLoader() if core_loader is None else core_loader
    binding=None
    if data_root is not None and hasattr(sessions,'pointers'):
        store=Mem0Store(client if client is not None else Mem0Client(credentials(),config.mem0_api_url,config.request_timeout_seconds),sessions.pointers)
        experiences=Mem0ExperienceRepository(store) if experiences is None else experiences
        controls=Mem0ControlRepository(store) if controls is None else controls
        binding=SessionBinding(sessions.pointers,controls,sessions.resolve_scope,sessions.host_id)
    experiences=UnconfiguredExperienceRepository() if experiences is None else experiences
    controls=UnconfiguredControlStateRepository() if controls is None else controls
    status=(('experience','configured' if binding else 'NOT_CONFIGURED'),
            ('control_state','configured' if binding else 'NOT_CONFIGURED'),
            ('session_runtime','host_contract_unverified' if binding else 'local_only'))
    def load_fixed(cwd):
        from .infrastructure.scope import resolve_project_root
        root=resolve_project_root(cwd)
        text=core_loader.load(root,config.core_paths,config.retention_budget_bytes)
        if plugin_root is None:raise MemoryError(ErrorCode.CORE_INVALID)
        fixed=(plugin_root/'prompts/core.md').read_text(encoding='utf-8')
        return fixed+'\n'+text
    def hook_host():
        # Hook identity comes from decoded native input; current MCP metadata is not normally set here.
        try:return host_source()
        except MemoryError:return None
    operation_scope=lambda:deadline(config.hook_deadline_seconds)
    memory=MemoryApplicationService(experiences,controls,sessions,enabled=config.enabled,core_loader=core_loader,
        binding=binding,host_source=host_source,operation_scope=operation_scope)
    retention=RetentionApplicationService(experiences,controls,sessions,enabled=config.enabled,binding=binding,
        host_source=hook_host,core_loader=load_fixed,budget_bytes=config.retention_budget_bytes,operation_scope=operation_scope)
    # use() needs request identity, never the Hook fallback.
    if retention.registry is not None:retention.registry.host_source=host_source
    return Runtime(memory,retention,experiences,controls,sessions,status,core_loader,binding)
