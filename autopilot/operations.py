"""Private server descriptors. Not MCP input schemas or proof of live guards."""
from dataclasses import dataclass

from autopilot.arguments import JsonValue, freeze_json, thaw_json
from autopilot.models import ContractBinding
from autopilot.paths import RootRef, Scope, absolute_path, hex_value, relative_target, text
from autopilot.rules import RESOURCE_TYPES, resource_key


class OperationError(ValueError):
    pass


def _boolean(value):
    if type(value) is not bool:
        raise OperationError('invalid_operation')


@dataclass(frozen=True, slots=True, repr=False)
class FileTarget:
    root: RootRef
    path: str
    directory: bool = False
    recursive: bool = False

    def __post_init__(self):
        if type(self.root) is not RootRef:
            raise OperationError('invalid_operation')
        _boolean(self.directory)
        _boolean(self.recursive)
        if self.recursive and not self.directory:
            raise OperationError('invalid_operation')
        object.__setattr__(self, 'path', absolute_path(self.root.platform, self.path))
        relative_target(self.root, self.path)


@dataclass(frozen=True, slots=True, repr=False)
class ResourceTarget:
    resource: object
    scope: object = None

    def __post_init__(self):
        if type(self.resource) not in RESOURCE_TYPES or self.scope is not None and type(self.scope) is not Scope:
            raise OperationError('invalid_operation')


@dataclass(frozen=True, slots=True, repr=False)
class OperationDescriptor:
    binding: ContractBinding
    tool: str
    action: str
    files: tuple = ()
    targets: tuple = ()
    resource: object = None
    overwrite: bool = False
    script: str = ''
    args: tuple = ()
    timeout_s: object = None
    tool_name: str = ''
    schema_digest: str = ''
    arguments: object = None

    def __post_init__(self):
        if type(self.binding) is not ContractBinding:
            raise OperationError('invalid_operation')
        text(self.tool)
        text(self.action)
        for items, model in ((self.files, FileTarget), (self.targets, ResourceTarget)):
            if type(items) is not tuple or len(items) > 1000 or any(type(item) is not model for item in items):
                raise OperationError('invalid_operation')
        if self.resource is not None and type(self.resource) not in RESOURCE_TYPES:
            raise OperationError('invalid_operation')
        _boolean(self.overwrite)
        if (type(self.args) is not tuple or len(self.args) > 100
                or any(type(arg) is not str or '\x00' in arg for arg in self.args)):
            raise OperationError('invalid_operation')
        freeze_json(list(self.args))
        for value in (self.script, self.tool_name, self.schema_digest):
            if type(value) is not str or len(value) > 4096:
                raise OperationError('invalid_operation')
        if self.schema_digest:
            hex_value(self.schema_digest, 64)
        if self.timeout_s is not None and (type(self.timeout_s) is not int or not 1 <= self.timeout_s <= 300):
            raise OperationError('invalid_operation')
        # Copy raw JSON; revalidate frozen server data on dataclasses.replace.
        arguments = thaw_json(self.arguments) if type(self.arguments) is JsonValue else self.arguments
        object.__setattr__(self, 'arguments', freeze_json(arguments))


@dataclass(frozen=True, slots=True, repr=False)
class ResourceAccess:
    resource: object
    checked: bool = False
    available: bool = False
    enabled: bool = False
    trusted: bool = False
    approval_required: bool = True
    trusted_tools: tuple = ()
    restricted: bool = False

    def __post_init__(self):
        if type(self.resource) not in RESOURCE_TYPES:
            raise OperationError('invalid_access')
        for value in (self.checked, self.available, self.enabled, self.trusted,
                      self.approval_required, self.restricted):
            _boolean(value)
        if (type(self.trusted_tools) is not tuple
                or len(self.trusted_tools) > 1000
                or any(type(item) is not tuple or len(item) != 2
                       or any(type(part) is not str for part in item) for item in self.trusted_tools)
                or len({item[0] for item in self.trusted_tools}) != len(self.trusted_tools)):
            raise OperationError('invalid_access')
        for name, digest in self.trusted_tools:
            text(name)
            hex_value(digest, 64)


@dataclass(frozen=True, slots=True, repr=False)
class FileAccess:
    root: RootRef
    path: str
    owner_mode: str
    checked: bool = False
    available: bool = False
    sensitive: bool = False
    restricted: bool = False

    def __post_init__(self):
        if (type(self.root) is not RootRef or type(self.owner_mode) is not str
                or self.owner_mode not in ('ro', 'rw')):
            raise OperationError('invalid_access')
        object.__setattr__(self, 'path', absolute_path(self.root.platform, self.path))
        relative_target(self.root, self.path)
        for value in (self.checked, self.available, self.sensitive, self.restricted):
            _boolean(value)


@dataclass(frozen=True, slots=True, repr=False)
class AccessSnapshot:
    binding: ContractBinding
    resources: tuple = ()
    files: tuple = ()

    def __post_init__(self):
        if type(self.binding) is not ContractBinding:
            raise OperationError('invalid_access')
        for values, model in ((self.resources, ResourceAccess), (self.files, FileAccess)):
            if type(values) is not tuple or len(values) > 1000 or any(type(item) is not model for item in values):
                raise OperationError('invalid_access')
        keys = [resource_key(row.resource) for row in self.resources]
        file_keys = [(resource_key(row.root), row.path) for row in self.files]
        if len(set(keys)) != len(keys) or len(set(file_keys)) != len(file_keys):
            raise OperationError('ambiguous_access')
