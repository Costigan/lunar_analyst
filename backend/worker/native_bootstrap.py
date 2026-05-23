from __future__ import annotations

import json
import os
import sys
import struct
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
import tomllib

from backend.core.config import APP_CONFIG_ENV
from backend.core.config import resolve_config_path as core_resolve_config_path
from backend.core.config import resolve_config_relative_path as core_resolve_config_relative_path
from backend.worker.gdal_runtime import resolve_gdal_data_dir, resolve_proj_data_dir

MOONLIB_DLL_ENV = "LUNAR_ANALYST_MOONLIB_DLL"
DOTNET_RUNTIME_CONFIG_ENV = "LUNAR_ANALYST_DOTNET_RUNTIME_CONFIG"
NUGET_PACKAGES_ENV = "NUGET_PACKAGES"
VALID_BUILD_PROFILES = {"debug", "release", "auto_newest"}
VALID_DLL_RESOLVER_MODES = {"strict", "legacy_path"}
_BUILD_CONFIG_DIRS = {"debug", "release"}
_ARCH_DIRS = {"x64", "x86"}
_LINUX_RID = "linux-x64"


class NativeBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeBootstrapConfig:
    moonlib_dll: Path | None = None
    dotnet_runtime_config: Path | None = None
    expected_target_framework: str = "net9.0"
    build_profile: str = "debug"
    dll_resolver_mode: str = "strict"
    dll_resolver_search_dirs: tuple[Path, ...] = field(default_factory=tuple)
    dll_resolver_imports: tuple[tuple[str, Path], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NativeRuntimeHandle:
    runtime: str
    moonlib_dll: Path
    dotnet_runtime_config: Path | None
    expected_target_framework: str
    smoke_check: dict[str, Any]


_BOOTSTRAP_CACHE: NativeRuntimeHandle | None = None
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_config_path() -> Path:
    return core_resolve_config_path()


def _resolve_path_from_config(raw_path: str, config_path: Path) -> Path:
    return core_resolve_config_relative_path(raw_path, config_path=config_path)


def _normalize_import_name(name: str) -> str:
    raw = str(name).strip().lower()
    if raw.startswith("lib") and raw.endswith(".so"):
        return raw[3:-3]
    if raw.endswith(".so"):
        return raw[:-3]
    if raw.endswith(".dylib"):
        trimmed = raw[:-6]
        return trimmed[3:] if trimmed.startswith("lib") else trimmed
    if raw.endswith(".dll"):
        return raw[:-4]
    return raw


def _iter_import_entries(raw: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for key, value in raw.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        full_key = f"{prefix}.{key_text}" if prefix else key_text
        if isinstance(value, dict):
            entries.extend(_iter_import_entries(value, full_key))
            continue
        entries.append((full_key, value))
    return entries


def load_native_bootstrap_config() -> NativeBootstrapConfig:
    config_path = _resolve_config_path()
    if not config_path.exists():
        return NativeBootstrapConfig()

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    backend = data.get("backend", {})
    native = backend.get("native", {})
    if not isinstance(native, dict):
        raise NativeBootstrapError(
            f"Invalid config format for [backend.native] in {config_path}"
        )

    moonlib_raw = native.get("moonlib_dll_path")
    runtimeconfig_raw = native.get("dotnet_runtime_config_path")
    expected_tfm = native.get("expected_target_framework", "net9.0")
    build_profile = native.get("build_profile", "debug")
    resolver = native.get("dll_resolver", {})

    moonlib_path: Path | None = None
    runtimeconfig_path: Path | None = None
    resolver_mode = "strict"
    resolver_search_dirs: list[Path] = []
    resolver_imports: list[tuple[str, Path]] = []
    if isinstance(moonlib_raw, str) and moonlib_raw.strip():
        moonlib_path = _resolve_path_from_config(moonlib_raw, config_path)
    if isinstance(runtimeconfig_raw, str) and runtimeconfig_raw.strip():
        runtimeconfig_path = _resolve_path_from_config(runtimeconfig_raw, config_path)
    if not isinstance(expected_tfm, str) or not expected_tfm.strip():
        raise NativeBootstrapError(
            f"Invalid expected_target_framework in {config_path}"
        )
    if not isinstance(build_profile, str) or build_profile.strip().lower() not in VALID_BUILD_PROFILES:
        raise NativeBootstrapError(
            f"Invalid build_profile in {config_path}. Expected one of: "
            + ", ".join(sorted(VALID_BUILD_PROFILES))
        )
    if resolver is not None and not isinstance(resolver, dict):
        raise NativeBootstrapError(f"Invalid [backend.native.dll_resolver] in {config_path}")
    if isinstance(resolver, dict):
        mode_raw = resolver.get("mode", "strict")
        if not isinstance(mode_raw, str) or mode_raw.strip().lower() not in VALID_DLL_RESOLVER_MODES:
            raise NativeBootstrapError(
                f"Invalid backend.native.dll_resolver.mode in {config_path}. Expected one of: "
                + ", ".join(sorted(VALID_DLL_RESOLVER_MODES))
            )
        resolver_mode = mode_raw.strip().lower()
        search_dirs_raw = resolver.get("search_dirs", [])
        if search_dirs_raw is not None:
            if not isinstance(search_dirs_raw, list) or not all(
                isinstance(item, str) for item in search_dirs_raw
            ):
                raise NativeBootstrapError(
                    f"Invalid backend.native.dll_resolver.search_dirs in {config_path}"
                )
            resolver_search_dirs = [
                _resolve_path_from_config(item, config_path)
                for item in search_dirs_raw
                if item.strip()
            ]
        imports_raw = resolver.get("imports", {})
        if imports_raw is not None:
            if not isinstance(imports_raw, dict):
                raise NativeBootstrapError(
                    f"Invalid backend.native.dll_resolver.imports in {config_path}"
                )
            for raw_name, raw_path in _iter_import_entries(imports_raw):
                if not isinstance(raw_name, str) or not raw_name.strip():
                    raise NativeBootstrapError(
                        f"Invalid import name in backend.native.dll_resolver.imports in {config_path}"
                    )
                if not isinstance(raw_path, str) or not raw_path.strip():
                    raise NativeBootstrapError(
                        f"Invalid import path for '{raw_name}' in {config_path}"
                    )
                resolver_imports.append(
                    (_normalize_import_name(raw_name), _resolve_path_from_config(raw_path, config_path))
                )

    return NativeBootstrapConfig(
        moonlib_dll=moonlib_path,
        dotnet_runtime_config=runtimeconfig_path,
        expected_target_framework=expected_tfm.strip(),
        build_profile=build_profile.strip().lower(),
        dll_resolver_mode=resolver_mode,
        dll_resolver_search_dirs=tuple(resolver_search_dirs),
        dll_resolver_imports=tuple(resolver_imports),
    )


def _build_output_dirs(repo_root: Path) -> dict[str, list[Path]]:
    return {
        "debug": [
            repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0" / _LINUX_RID,
            repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0",
            repo_root / "native" / "new_horizon" / "moonlib_host" / "bin" / "Debug" / "net9.0" / "runtimes" / _LINUX_RID / "native",
            repo_root / "native" / "new_horizon" / "moonlib_host" / "bin" / "Debug" / "net9.0",
            repo_root / ".." / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0" / _LINUX_RID,
            repo_root / ".." / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0",
            repo_root / ".." / "new_horizon" / "bin" / "Debug" / "net9.0" / _LINUX_RID,
            repo_root / ".." / "new_horizon" / "bin" / "Debug" / "net9.0",
        ],
        "release": [
            repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Release" / "net9.0" / _LINUX_RID,
            repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Release" / "net9.0",
            repo_root / "native" / "new_horizon" / "moonlib_host" / "bin" / "Release" / "net9.0" / "runtimes" / _LINUX_RID / "native",
            repo_root / "native" / "new_horizon" / "moonlib_host" / "bin" / "Release" / "net9.0",
            repo_root / ".." / "new_horizon" / "moonlib" / "bin" / "Release" / "net9.0" / _LINUX_RID,
            repo_root / ".." / "new_horizon" / "moonlib" / "bin" / "Release" / "net9.0",
            repo_root / ".." / "new_horizon" / "bin" / "Release" / "net9.0" / _LINUX_RID,
            repo_root / ".." / "new_horizon" / "bin" / "Release" / "net9.0",
        ],
    }


def _newest_artifact_mtime(dirs: list[Path], filename: str) -> float | None:
    newest: float | None = None
    for directory in dirs:
        candidate = directory / filename
        if not candidate.exists():
            continue
        mtime = candidate.stat().st_mtime
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def _build_preference(cfg: NativeBootstrapConfig, repo_root: Path) -> list[str]:
    if cfg.build_profile == "debug":
        return ["debug", "release"]
    if cfg.build_profile == "release":
        return ["release", "debug"]

    outputs = _build_output_dirs(repo_root)
    debug_mtime = _newest_artifact_mtime(outputs["debug"], "moonlib.dll")
    release_mtime = _newest_artifact_mtime(outputs["release"], "moonlib.dll")
    if debug_mtime is None and release_mtime is None:
        return ["debug", "release"]
    if debug_mtime is None:
        return ["release", "debug"]
    if release_mtime is None:
        return ["debug", "release"]
    if debug_mtime >= release_mtime:
        return ["debug", "release"]
    return ["release", "debug"]


def _default_moonlib_candidates(repo_root: Path, cfg: NativeBootstrapConfig) -> list[Path]:
    outputs = _build_output_dirs(repo_root)
    candidates: list[Path] = []
    for profile in _build_preference(cfg, repo_root):
        candidates.extend([directory / "moonlib.dll" for directory in outputs[profile]])
    return candidates


def _default_runtimeconfig_candidates(
    repo_root: Path, cfg: NativeBootstrapConfig
) -> list[Path]:
    outputs = _build_output_dirs(repo_root)
    candidates: list[Path] = []
    for profile in _build_preference(cfg, repo_root):
        candidates.extend(
            [
                directory / "moonlib.runtimeconfig.json"
                for directory in outputs[profile]
            ]
        )
        candidates.extend(
            [
                directory / "moonlib_host.runtimeconfig.json"
                for directory in outputs[profile]
            ]
        )
    return candidates


def _cspice_filename() -> str:
    return "libcspice.so"


def _default_cspice_candidates(repo_root: Path) -> list[Path]:
    filename = _cspice_filename()
    candidates = [
        repo_root / "native" / "third_party" / "cspice" / "linux-x64" / "libcspice.so",
    ]
    outputs = _build_output_dirs(repo_root)
    for profile_dirs in outputs.values():
        candidates.extend(directory / filename for directory in profile_dirs)
    candidates.extend(
        [
            repo_root / "native" / "new_horizon" / "moonlib" / filename,
            repo_root / ".." / "new_horizon" / "moonlib" / filename,
        ]
    )
    return candidates


def _resolve_existing_path(path: Path | None, fallback: list[Path]) -> Path:
    if path is not None:
        candidate = path.expanduser().resolve()
        if candidate.exists():
            return candidate
        for compatible in _iter_compatible_artifact_paths(candidate):
            if compatible.exists():
                return compatible.resolve()
        for alt in fallback:
            resolved = alt.expanduser().resolve()
            if resolved.exists():
                return resolved
        raise NativeBootstrapError(f"Path does not exist: {candidate}")

    for candidate in fallback:
        resolved = candidate.expanduser().resolve()
        if resolved.exists():
            return resolved

    raise NativeBootstrapError(
        "Unable to find native artifact. Set explicit path using environment variables."
    )


def _iter_compatible_artifact_paths(path: Path) -> list[Path]:
    parts = list(path.parts)
    if not parts:
        return []

    candidates: list[Path] = []
    arch_dir = "x64" if struct.calcsize("P") == 8 else "x86"

    for index in range(len(parts) - 2):
        if parts[index].lower() != "bin":
            continue
        if parts[index + 1].lower() in _ARCH_DIRS and parts[index + 2].lower() in _BUILD_CONFIG_DIRS:
            without_arch = Path(parts[0], *parts[1:index + 1], *parts[index + 2 :])
            if len(parts) > index + 3 and parts[index + 3].lower() == "net9.0":
                candidates.append(
                    Path(
                        parts[0],
                        *parts[1:index + 1],
                        *parts[index + 2 : index + 4],
                        _LINUX_RID,
                        *parts[index + 4 :],
                    )
                )
            candidates.append(without_arch)

    for index in range(len(parts) - 1):
        if parts[index].lower() != "bin":
            continue
        if parts[index + 1].lower() in _BUILD_CONFIG_DIRS:
            candidates.append(
                Path(parts[0], *parts[1:index + 1], arch_dir, *parts[index + 1 :])
            )
        if (
            parts[index + 1].lower() in _BUILD_CONFIG_DIRS
            and len(parts) > index + 2
            and parts[index + 2].lower() == "net9.0"
            and (len(parts) <= index + 3 or parts[index + 3].lower() != _LINUX_RID)
        ):
            candidates.append(
                Path(
                    parts[0],
                    *parts[1:index + 1],
                    *parts[index + 1 : index + 3],
                    _LINUX_RID,
                    *parts[index + 3 :],
                )
            )

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def resolve_moonlib_dll(config: NativeBootstrapConfig | None = None) -> Path:
    cfg = config or NativeBootstrapConfig()
    repo_root = _repo_root()

    env_override = os.getenv(MOONLIB_DLL_ENV)
    if env_override:
        return _resolve_existing_path(Path(env_override), [])

    return _resolve_existing_path(cfg.moonlib_dll, _default_moonlib_candidates(repo_root, cfg))


def resolve_runtimeconfig(
    config: NativeBootstrapConfig | None = None,
    *,
    moonlib_dll: Path | None = None,
) -> Path | None:
    cfg = config or NativeBootstrapConfig()
    repo_root = _repo_root()

    env_override = os.getenv(DOTNET_RUNTIME_CONFIG_ENV)
    if env_override:
        return _resolve_existing_path(Path(env_override), [])

    if cfg.dotnet_runtime_config is not None:
        return _resolve_existing_path(cfg.dotnet_runtime_config, [])

    if moonlib_dll is not None:
        same_dir_runtimeconfig = moonlib_dll.with_name("moonlib.runtimeconfig.json")
        if same_dir_runtimeconfig.exists():
            return same_dir_runtimeconfig.resolve()

    for candidate in _default_runtimeconfig_candidates(repo_root, cfg):
        resolved = candidate.expanduser().resolve()
        if resolved.exists():
            return resolved
    return None


def _validate_target_framework(path: Path, expected_target_framework: str) -> None:
    if expected_target_framework not in path.parts:
        raise NativeBootstrapError(
            f"Expected target framework '{expected_target_framework}' in path: {path}"
        )


def _default_nuget_packages_dir() -> Path:
    env_override = os.getenv(NUGET_PACKAGES_ENV)
    if env_override:
        return Path(env_override).expanduser().resolve()
    return (Path.home() / ".nuget" / "packages").resolve()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in paths:
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _infer_default_native_imports(moonlib_dll: Path, arch_dir: str) -> list[tuple[str, Path]]:
    repo_root = _repo_root()
    root = moonlib_dll.parent.resolve()
    ordered_names = ["gdal", "gdal_wrap", "gdalconst_wrap", "cspice"]
    linux_names = {
        "gdal": ("libgdal.so.37", "libgdal.so"),
        "gdal_wrap": ("libgdal_wrap.so",),
        "gdalconst_wrap": ("libgdalconst_wrap.so",),
    }
    inferred: list[tuple[str, Path]] = []
    for name in ordered_names:
        if name == "cspice":
            candidate = next(
                (
                    resolved
                    for resolved in [root / _cspice_filename(), *_default_cspice_candidates(repo_root)]
                    if resolved.exists()
                ),
                root / _cspice_filename(),
            )
        else:
            candidate = next(
                (
                    root / filename
                    for filename in linux_names[name]
                    if (root / filename).exists()
                ),
                root / linux_names[name][0],
            )
        if candidate.exists():
            inferred.append((name, candidate.resolve()))
    return inferred


def _resolve_configured_search_dirs(search_dirs: tuple[Path, ...]) -> list[Path]:
    resolved: list[Path] = []
    for candidate in search_dirs:
        explicit = candidate.resolve()
        if explicit.exists():
            resolved.append(explicit)
            continue
        for compatible in _iter_compatible_artifact_paths(explicit):
            compatible_resolved = compatible.resolve()
            if compatible_resolved.exists():
                resolved.append(compatible_resolved)
                break
    return _dedupe_paths(resolved)


def _resolve_configured_import_path(
    import_name: str,
    import_path: Path,
    *,
    moonlib_dll: Path,
    inferred_imports: list[tuple[str, Path]],
) -> Path:
    candidate = import_path.resolve()
    if candidate.exists():
        return candidate

    for compatible in _iter_compatible_artifact_paths(candidate):
        compatible_resolved = compatible.resolve()
        if compatible_resolved.exists():
            return compatible_resolved

    for inferred_name, inferred_path in inferred_imports:
        if inferred_name == import_name and inferred_path.exists():
            return inferred_path.resolve()

    local_roots = [
        moonlib_dll.parent.resolve(),
        (moonlib_dll.parent / "gdal").resolve(),
        (moonlib_dll.parent / "gdal" / ("x64" if struct.calcsize("P") == 8 else "x86")).resolve(),
    ]
    for root in local_roots:
        basename_candidate = root / import_path.name
        if basename_candidate.exists():
            return basename_candidate.resolve()

    raise NativeBootstrapError(
        f"Native resolver import '{import_name}' path does not exist: {candidate}"
    )


def _configure_native_dll_search_paths(
    moonlib_dll: Path,
    config: NativeBootstrapConfig,
) -> None:
    # Ensure native dependencies (for example GDAL/CSPICE) are discoverable.
    arch_dir = "x64" if struct.calcsize("P") == 8 else "x86"
    gdal_root = moonlib_dll.parent / "gdal"
    gdal_arch_dir = gdal_root / arch_dir
    strict_mode = config.dll_resolver_mode == "strict"

    inferred_imports = _infer_default_native_imports(moonlib_dll, arch_dir)
    configured_dirs = _resolve_configured_search_dirs(config.dll_resolver_search_dirs)
    configured_imports = [
        (
            import_name,
            _resolve_configured_import_path(
                import_name,
                import_path,
                moonlib_dll=moonlib_dll,
                inferred_imports=inferred_imports,
            ),
        )
        for import_name, import_path in config.dll_resolver_imports
    ]
    import_parent_dirs = [path.parent.resolve() for _, path in configured_imports]
    candidates: list[Path] = [moonlib_dll.parent.resolve()]
    runtime_native_dirs = list((moonlib_dll.parent / "runtimes").glob("*/native"))
    runtime_native_dirs = [path.resolve() for path in runtime_native_dirs if path.exists()]
    if moonlib_dll.parent.name != _LINUX_RID:
        rid_sibling = moonlib_dll.parent / _LINUX_RID
        if not rid_sibling.exists() and moonlib_dll.parent.name.lower() == "net9.0":
            rid_sibling = moonlib_dll.parent / _LINUX_RID
    else:
        rid_sibling = moonlib_dll.parent
    if configured_dirs:
        candidates.append(rid_sibling.resolve())
        candidates.extend(configured_dirs)
        candidates.extend(import_parent_dirs)
    else:
        candidates.extend(
            [
                rid_sibling.resolve(),
                (gdal_arch_dir / "plugins").resolve(),
                *runtime_native_dirs,
            ]
        )

    deduped_candidates = _dedupe_paths(candidates)

    existing_path = os.environ.get("PATH", "")
    existing_ld_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    for candidate in deduped_candidates:
        if not candidate.exists():
            continue
        candidate_text = str(candidate)
        if not strict_mode:
            path_parts = existing_path.split(os.pathsep) if existing_path else []
            if candidate_text not in path_parts:
                existing_path = f"{candidate_text}{os.pathsep}{existing_path}" if existing_path else candidate_text
                os.environ["PATH"] = existing_path
        ld_parts = existing_ld_library_path.split(os.pathsep) if existing_ld_library_path else []
        if candidate_text not in ld_parts:
            existing_ld_library_path = (
                f"{candidate_text}{os.pathsep}{existing_ld_library_path}"
                if existing_ld_library_path
                else candidate_text
            )
            os.environ["LD_LIBRARY_PATH"] = existing_ld_library_path

    native_gdal_data_dir, native_proj_data_dir = _resolve_native_gdal_data_dirs(
        moonlib_dll, deduped_candidates
    )
    if native_gdal_data_dir is not None:
        os.environ["GDAL_DATA"] = str(native_gdal_data_dir)
    else:
        gdal_data_dir = resolve_gdal_data_dir()
        if gdal_data_dir is not None and not os.getenv("GDAL_DATA"):
            os.environ["GDAL_DATA"] = str(gdal_data_dir)
    if native_proj_data_dir is not None:
        os.environ["PROJ_LIB"] = str(native_proj_data_dir)
        os.environ["PROJ_DATA"] = str(native_proj_data_dir)
    else:
        proj_data_dir = resolve_proj_data_dir()
        if proj_data_dir is not None:
            if not os.getenv("PROJ_LIB"):
                os.environ["PROJ_LIB"] = str(proj_data_dir)
            if not os.getenv("PROJ_DATA"):
                os.environ["PROJ_DATA"] = str(proj_data_dir)


def _iter_native_bundle_roots(moonlib_dll: Path, search_dirs: list[Path]) -> list[Path]:
    candidates: list[Path] = [
        moonlib_dll.parent.resolve(),
        (moonlib_dll.parent / "gdal").resolve(),
    ]
    candidates.extend(search_dirs)

    for candidate in list(candidates):
        if candidate.name.lower() in _ARCH_DIRS:
            candidates.append(candidate.parent.resolve())
        gdal_child = candidate / "gdal"
        if gdal_child.exists():
            candidates.append(gdal_child.resolve())

    deps_json = moonlib_dll.with_suffix(".deps.json")
    if deps_json.exists():
        try:
            data = json.loads(deps_json.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        libraries = data.get("libraries", {})
        if isinstance(libraries, dict):
            for library_key in libraries:
                if not isinstance(library_key, str) or "/" not in library_key:
                    continue
                package_id, package_version = library_key.split("/", 1)
                if package_id.lower() != "gdal.native":
                    continue
                nuget_root = _default_nuget_packages_dir() / package_id.lower() / package_version / "build" / "gdal"
                candidates.append(nuget_root.resolve())

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _extract_native_bundle_data_dirs(root: Path) -> tuple[Path | None, Path | None]:
    gdal_data_candidates = [root / "data", root / "gdal-data"]
    proj_data_candidates = [root / "share", root / "proj-lib", root / "share" / "proj"]

    gdal_data_dir = next((path.resolve() for path in gdal_data_candidates if path.exists()), None)
    proj_data_dir = next(
        (
            path.resolve()
            for path in proj_data_candidates
            if path.exists() and ((path / "proj.db").exists() or path.name.lower() == "proj")
        ),
        None,
    )
    return gdal_data_dir, proj_data_dir


def _resolve_native_gdal_data_dirs(
    moonlib_dll: Path, search_dirs: list[Path]
) -> tuple[Path | None, Path | None]:
    for root in _iter_native_bundle_roots(moonlib_dll, search_dirs):
        gdal_data_dir, proj_data_dir = _extract_native_bundle_data_dirs(root)
        if gdal_data_dir is not None or proj_data_dir is not None:
            return gdal_data_dir, proj_data_dir
    return None, None


def _iter_managed_dependency_candidates(moonlib_dll: Path) -> list[Path]:
    deps_json = moonlib_dll.with_suffix(".deps.json")
    if not deps_json.exists():
        return []

    try:
        data = json.loads(deps_json.read_text(encoding="utf-8"))
    except Exception:
        return []

    targets = data.get("targets", {})
    if not isinstance(targets, dict) or not targets:
        return []

    target_name = next(iter(targets.keys()))
    target = targets.get(target_name, {})
    if not isinstance(target, dict):
        return []

    nuget_root = _default_nuget_packages_dir()
    candidates: list[Path] = []
    seen: set[str] = set()
    for library_key, library_value in target.items():
        if not isinstance(library_key, str) or "/" not in library_key:
            continue
        package_id, package_version = library_key.split("/", 1)
        if package_id.lower() == "moonlib":
            continue
        if not isinstance(library_value, dict):
            continue
        runtime_assets = library_value.get("runtime", {})
        if not isinstance(runtime_assets, dict):
            continue
        for rel_path in runtime_assets.keys():
            if not isinstance(rel_path, str) or not rel_path.lower().endswith(".dll"):
                continue
            dll_name = Path(rel_path).name
            if dll_name.lower() in seen:
                continue
            local_copy = moonlib_dll.parent / dll_name
            if local_copy.exists():
                candidates.append(local_copy.resolve())
                seen.add(dll_name.lower())
                continue
            nuget_copy = nuget_root / package_id.lower() / package_version / Path(rel_path)
            if nuget_copy.exists():
                candidates.append(nuget_copy.resolve())
                seen.add(dll_name.lower())
                continue
    return candidates


def _preload_managed_dependencies(moonlib_dll: Path) -> None:
    try:
        import clr  # type: ignore
    except Exception:
        return
    for dependency_path in _iter_managed_dependency_candidates(moonlib_dll):
        try:
            clr.AddReference(str(dependency_path))  # type: ignore[attr-defined]
        except Exception:
            # Some assemblies may not be required for the current execution path.
            continue


def _ensure_managed_probe_dir(moonlib_dll: Path) -> None:
    probe_dir = str(moonlib_dll.parent.resolve())
    if probe_dir not in sys.path:
        sys.path.append(probe_dir)


def run_bridge_smoke_check() -> dict[str, Any]:
    try:
        import moonlib  # type: ignore
    except Exception as exc:
        raise NativeBootstrapError(
            "Failed to import moonlib after assembly load."
        ) from exc

    try:
        bridge_smoke = moonlib.BridgeSmoke
        output_value = float(bridge_smoke.AddOne(1.0))
        spice_output = int(bridge_smoke.SpiceSmokeTest(1))
    except Exception as exc:
        raise NativeBootstrapError(
            "Failed to execute moonlib.BridgeSmoke smoke methods."
        ) from exc

    if abs(output_value - 2.0) > 1e-6:
        raise NativeBootstrapError(
            f"Bridge smoke check failed: expected 2.0, got {output_value}."
        )
    if spice_output != 2:
        raise NativeBootstrapError(
            f"Spice smoke check failed: expected 2, got {spice_output}."
        )

    try:
        gdal_data = str(moonlib.MoonlibBridge.GdalSmokeTest() or "")
    except Exception as exc:
        raise NativeBootstrapError("Failed to execute moonlib GDAL smoke check.") from exc

    return {
        "type": "moonlib.BridgeSmoke",
        "add_one_input": 1.0,
        "add_one_output": output_value,
        "spice_input": 1,
        "spice_output": spice_output,
        "gdal_config_probe": True,
        "gdal_data": gdal_data,
    }


def bootstrap_pythonnet(
    config: NativeBootstrapConfig | None = None,
    *,
    force: bool = False,
    verify_bridge_smoke: bool = True,
) -> NativeRuntimeHandle:
    global _BOOTSTRAP_CACHE
    if _BOOTSTRAP_CACHE is not None and not force:
        return _BOOTSTRAP_CACHE

    cfg = config or load_native_bootstrap_config()
    moonlib_dll = resolve_moonlib_dll(cfg)
    _validate_target_framework(moonlib_dll, cfg.expected_target_framework)
    runtime_config = resolve_runtimeconfig(cfg, moonlib_dll=moonlib_dll)

    try:
        from pythonnet import load as pythonnet_load
    except Exception as exc:  # pragma: no cover
        raise NativeBootstrapError(
            "pythonnet is unavailable. Install pythonnet in the active environment."
        ) from exc

    try:
        if runtime_config is None:
            pythonnet_load("coreclr")
        else:
            pythonnet_load("coreclr", runtime_config=str(runtime_config))
    except Exception as exc:
        raise NativeBootstrapError("Failed to initialize pythonnet coreclr runtime.") from exc

    try:
        import clr  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise NativeBootstrapError("pythonnet clr module is unavailable after runtime load.") from exc

    _configure_native_dll_search_paths(moonlib_dll, cfg)
    _preload_managed_dependencies(moonlib_dll)
    _ensure_managed_probe_dir(moonlib_dll)

    try:
        clr.AddReference("moonlib")  # type: ignore[attr-defined]
    except Exception as exc:
        raise NativeBootstrapError(f"Failed to add moonlib assembly reference: {moonlib_dll}") from exc

    smoke_check = {"type": "skipped"}
    if verify_bridge_smoke:
        smoke_check = run_bridge_smoke_check()

    handle = NativeRuntimeHandle(
        runtime="coreclr",
        moonlib_dll=moonlib_dll,
        dotnet_runtime_config=runtime_config,
        expected_target_framework=cfg.expected_target_framework,
        smoke_check=smoke_check,
    )
    _BOOTSTRAP_CACHE = handle
    return handle


def import_moonlib(
    config: NativeBootstrapConfig | None = None,
    *,
    force_bootstrap: bool = False,
    verify_bridge_smoke: bool = True,
) -> Any:
    bootstrap_pythonnet(
        config=config,
        force=force_bootstrap,
        verify_bridge_smoke=verify_bridge_smoke,
    )
    try:
        import moonlib  # type: ignore
    except Exception as exc:
        raise NativeBootstrapError("Failed to import moonlib after bootstrap.") from exc
    return moonlib


def bootstrap_status() -> dict[str, Any]:
    if _BOOTSTRAP_CACHE is None:
        return {"loaded": False}
    return {
        "loaded": True,
        "runtime": _BOOTSTRAP_CACHE.runtime,
        "moonlib_dll": str(_BOOTSTRAP_CACHE.moonlib_dll),
        "dotnet_runtime_config": (
            str(_BOOTSTRAP_CACHE.dotnet_runtime_config)
            if _BOOTSTRAP_CACHE.dotnet_runtime_config is not None
            else None
        ),
        "expected_target_framework": _BOOTSTRAP_CACHE.expected_target_framework,
        "smoke_check": _BOOTSTRAP_CACHE.smoke_check,
    }


def reset_bootstrap_cache() -> None:
    global _BOOTSTRAP_CACHE
    _BOOTSTRAP_CACHE = None
