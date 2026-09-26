"""package-lock.json to the set of name@version pairs it installs from the npm registry."""

import json
from typing import Any

Pair = tuple[str, str]


class LockfileError(ValueError):
    pass


def _registry_version(version: Any) -> str | None:
    # file:, link:, git+ssh:, github:owner/repo and the like are not npm registry versions.
    if not isinstance(version, str) or not version or ":" in version or "/" in version:
        return None
    return version


def _from_packages(packages: dict[str, Any]) -> set[Pair]:
    """lockfileVersion 2 and 3: keys are install paths like node_modules/@scope/name."""
    pairs = set()
    for path, entry in packages.items():
        if "node_modules/" not in path or not isinstance(entry, dict) or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[1]
        version = _registry_version(entry.get("version"))
        if isinstance(name, str) and name and version:
            pairs.add((name, version))
    return pairs


def _from_dependencies(dependencies: dict[str, Any]) -> set[Pair]:
    """lockfileVersion 1: a tree of name -> {version, dependencies}."""
    pairs = set()
    stack = [dependencies]
    while stack:
        for name, entry in stack.pop().items():
            if not isinstance(entry, dict):
                continue
            version = _registry_version(entry.get("version"))
            if version:
                pairs.add((name, version))
            nested = entry.get("dependencies")
            if isinstance(nested, dict):
                stack.append(nested)
    return pairs


def parse_lockfile(text: str) -> set[Pair]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LockfileError(f"package-lock.json is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(data, dict):
        raise LockfileError("package-lock.json is not a JSON object")

    # Version 2 carries both sections for old npm clients; "packages" is the complete one.
    if isinstance(data.get("packages"), dict):
        return _from_packages(data["packages"])
    if isinstance(data.get("dependencies"), dict):
        return _from_dependencies(data["dependencies"])
    if data.get("lockfileVersion") in (1, 2, 3):
        return set()
    raise LockfileError("package-lock.json has neither packages nor dependencies")
