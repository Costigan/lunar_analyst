from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

import backend.worker.native_bootstrap as native_bootstrap
from backend.worker.native_bootstrap import (
    APP_CONFIG_ENV,
    MOONLIB_DLL_ENV,
    NativeBootstrapConfig,
    NativeBootstrapError,
    bootstrap_pythonnet,
    bootstrap_status,
    load_native_bootstrap_config,
    reset_bootstrap_cache,
    resolve_moonlib_dll,
    resolve_runtimeconfig,
)


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_bootstrap_cache()
    monkeypatch.delenv(MOONLIB_DLL_ENV, raising=False)


def test_bootstrap_pythonnet_loads_coreclr_and_adds_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tfm_dir = tmp_path / "net9.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    dll_path = tfm_dir / "moonlib.dll"
    dll_path.write_bytes(b"")

    calls: list[tuple[str, tuple, dict]] = []

    pythonnet_mod = types.ModuleType("pythonnet")

    def fake_load(*args, **kwargs):
        calls.append(("load", args, kwargs))

    pythonnet_mod.load = fake_load  # type: ignore[attr-defined]

    clr_mod = types.ModuleType("clr")

    def fake_add_reference(path: str):
        calls.append(("add_reference", (path,), {}))

    clr_mod.AddReference = fake_add_reference  # type: ignore[attr-defined]
    moonlib_mod = types.ModuleType("moonlib")

    class BridgeSmoke:
        @staticmethod
        def AddOne(x: float) -> float:
            return x + 1.0

        @staticmethod
        def SpiceSmokeTest(x: int) -> int:
            return x + 1

    class MoonlibBridge:
        @staticmethod
        def GdalSmokeTest() -> str:
            return ""

    moonlib_mod.BridgeSmoke = BridgeSmoke  # type: ignore[attr-defined]
    moonlib_mod.MoonlibBridge = MoonlibBridge  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pythonnet", pythonnet_mod)
    monkeypatch.setitem(sys.modules, "clr", clr_mod)
    monkeypatch.setitem(sys.modules, "moonlib", moonlib_mod)

    handle = bootstrap_pythonnet(
        NativeBootstrapConfig(moonlib_dll=dll_path, dotnet_runtime_config=None),
        force=True,
    )

    assert handle.runtime == "coreclr"
    assert handle.moonlib_dll == dll_path.resolve()
    assert calls[0][0] == "load"
    assert calls[0][1] == ("coreclr",)
    assert calls[1][0] == "add_reference"
    assert calls[1][1] == ("moonlib",)
    assert handle.smoke_check["type"] == "moonlib.BridgeSmoke"
    assert handle.smoke_check["add_one_output"] == 2.0
    assert handle.smoke_check["spice_output"] == 2
    assert handle.smoke_check["gdal_config_probe"] is True

    status = bootstrap_status()
    assert status["loaded"] is True
    assert status["runtime"] == "coreclr"
    assert status["smoke_check"]["add_one_output"] == 2.0
    assert status["smoke_check"]["spice_output"] == 2


def test_bootstrap_requires_net9_target_framework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tfm_dir = tmp_path / "net8.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    dll_path = tfm_dir / "moonlib.dll"
    dll_path.write_bytes(b"")

    pythonnet_mod = types.ModuleType("pythonnet")
    pythonnet_mod.load = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    clr_mod = types.ModuleType("clr")
    clr_mod.AddReference = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pythonnet", pythonnet_mod)
    monkeypatch.setitem(sys.modules, "clr", clr_mod)

    with pytest.raises(NativeBootstrapError, match="Expected target framework"):
        bootstrap_pythonnet(NativeBootstrapConfig(moonlib_dll=dll_path), force=True)


def test_bootstrap_requires_pythonnet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tfm_dir = tmp_path / "net9.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    dll_path = tfm_dir / "moonlib.dll"
    dll_path.write_bytes(b"")

    missing_pythonnet = types.ModuleType("pythonnet")
    monkeypatch.setitem(sys.modules, "pythonnet", missing_pythonnet)

    with pytest.raises(NativeBootstrapError, match="pythonnet is unavailable"):
        bootstrap_pythonnet(
            NativeBootstrapConfig(moonlib_dll=dll_path),
            force=True,
        )


def test_bridge_smoke_check_requires_expected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tfm_dir = tmp_path / "net9.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    dll_path = tfm_dir / "moonlib.dll"
    dll_path.write_bytes(b"")

    pythonnet_mod = types.ModuleType("pythonnet")
    pythonnet_mod.load = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    clr_mod = types.ModuleType("clr")
    clr_mod.AddReference = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    moonlib_mod = types.ModuleType("moonlib")

    class BridgeSmoke:
        @staticmethod
        def AddOne(x: float) -> float:
            return x + 2.0

        @staticmethod
        def SpiceSmokeTest(x: int) -> int:
            return x + 1

    moonlib_mod.BridgeSmoke = BridgeSmoke  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pythonnet", pythonnet_mod)
    monkeypatch.setitem(sys.modules, "clr", clr_mod)
    monkeypatch.setitem(sys.modules, "moonlib", moonlib_mod)

    with pytest.raises(NativeBootstrapError, match="Bridge smoke check failed"):
        bootstrap_pythonnet(NativeBootstrapConfig(moonlib_dll=dll_path), force=True)


def test_spice_smoke_check_requires_expected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tfm_dir = tmp_path / "net9.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    dll_path = tfm_dir / "moonlib.dll"
    dll_path.write_bytes(b"")

    pythonnet_mod = types.ModuleType("pythonnet")
    pythonnet_mod.load = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    clr_mod = types.ModuleType("clr")
    clr_mod.AddReference = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    moonlib_mod = types.ModuleType("moonlib")

    class BridgeSmoke:
        @staticmethod
        def AddOne(x: float) -> float:
            return x + 1.0

        @staticmethod
        def SpiceSmokeTest(x: int) -> int:
            return x + 2

    moonlib_mod.BridgeSmoke = BridgeSmoke  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pythonnet", pythonnet_mod)
    monkeypatch.setitem(sys.modules, "clr", clr_mod)
    monkeypatch.setitem(sys.modules, "moonlib", moonlib_mod)

    with pytest.raises(NativeBootstrapError, match="Spice smoke check failed"):
        bootstrap_pythonnet(NativeBootstrapConfig(moonlib_dll=dll_path), force=True)


def test_load_native_bootstrap_config_resolves_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    native_dir = tmp_path / "native" / "net9.0"
    native_dir.mkdir(parents=True, exist_ok=True)
    dll = native_dir / "moonlib.dll"
    runtimeconfig = native_dir / "moonlib.runtimeconfig.json"
    cspice = native_dir / "libcspice.so"
    dll.write_bytes(b"")
    runtimeconfig.write_text("{}", encoding="utf-8")
    cspice.write_bytes(b"")

    config_path = cfg_dir / "lunar_analyst.toml"
    config_path.write_text(
        "\n".join(
            [
                "[backend.native]",
                'moonlib_dll_path = "../native/net9.0/moonlib.dll"',
                'dotnet_runtime_config_path = "../native/net9.0/moonlib.runtimeconfig.json"',
                'expected_target_framework = "net9.0"',
                'build_profile = "auto_newest"',
                "",
                "[backend.native.dll_resolver]",
                'mode = "strict"',
                'search_dirs = ["../native/net9.0"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(config_path))

    cfg = load_native_bootstrap_config()
    assert cfg.moonlib_dll == dll.resolve()
    assert cfg.dotnet_runtime_config == runtimeconfig.resolve()
    assert cfg.expected_target_framework == "net9.0"
    assert cfg.build_profile == "auto_newest"
    assert cfg.dll_resolver_mode == "strict"
    assert cfg.dll_resolver_search_dirs == (native_dir.resolve(),)
    assert cfg.dll_resolver_imports == ()


def test_normalize_import_name_handles_cross_platform_cspice_names() -> None:
    assert native_bootstrap._normalize_import_name("cspice.dll") == "cspice"
    assert native_bootstrap._normalize_import_name("cspice.so") == "cspice"
    assert native_bootstrap._normalize_import_name("libcspice.so") == "cspice"


def test_infer_default_native_imports_uses_pinned_linux_cspice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path
    moonlib_dir = repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0"
    moonlib_dir.mkdir(parents=True, exist_ok=True)
    (moonlib_dir / "moonlib.dll").write_bytes(b"")

    pinned_cspice = repo_root / "native" / "third_party" / "cspice" / "linux-x64" / "libcspice.so"
    pinned_cspice.parent.mkdir(parents=True, exist_ok=True)
    pinned_cspice.write_bytes(b"")

    monkeypatch.setattr(native_bootstrap, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(native_bootstrap.os, "name", "posix")

    inferred = native_bootstrap._infer_default_native_imports(moonlib_dir / "moonlib.dll", "x64")
    assert ("cspice", pinned_cspice.resolve()) in inferred


def test_load_native_bootstrap_config_rejects_invalid_build_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    config_path.write_text(
        "\n".join(
            [
                "[backend.native]",
                'build_profile = "nightly"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(config_path))

    with pytest.raises(NativeBootstrapError, match="Invalid build_profile"):
        load_native_bootstrap_config()


def test_load_native_bootstrap_config_rejects_invalid_resolver_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    config_path.write_text(
        "\n".join(
            [
                "[backend.native.dll_resolver]",
                'mode = "mixed"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(config_path))

    with pytest.raises(NativeBootstrapError, match="Invalid backend.native.dll_resolver.mode"):
        load_native_bootstrap_config()


def test_auto_newest_prefers_newer_release_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path
    debug_dir = (
        repo_root
        / "native"
        / "new_horizon"
        / "moonlib"
        / "bin"
        / "Debug"
        / "net9.0"
        / "linux-x64"
    )
    release_dir = (
        repo_root
        / "native"
        / "new_horizon"
        / "moonlib"
        / "bin"
        / "Release"
        / "net9.0"
        / "linux-x64"
    )
    debug_dir.mkdir(parents=True, exist_ok=True)
    release_dir.mkdir(parents=True, exist_ok=True)

    debug_dll = debug_dir / "moonlib.dll"
    release_dll = release_dir / "moonlib.dll"
    debug_runtime = debug_dir / "moonlib.runtimeconfig.json"
    release_runtime = release_dir / "moonlib.runtimeconfig.json"
    debug_dll.write_bytes(b"debug")
    release_dll.write_bytes(b"release")
    debug_runtime.write_text("{}", encoding="utf-8")
    release_runtime.write_text("{}", encoding="utf-8")

    # Ensure release looks newer than debug.
    os.utime(debug_dll, (1000, 1000))
    os.utime(release_dll, (2000, 2000))

    monkeypatch.setattr(native_bootstrap, "_repo_root", lambda: repo_root)

    cfg = NativeBootstrapConfig(build_profile="auto_newest")
    selected_dll = resolve_moonlib_dll(cfg)
    selected_runtime = resolve_runtimeconfig(cfg, moonlib_dll=selected_dll)
    assert selected_dll == release_dll.resolve()
    assert selected_runtime == release_runtime.resolve()


def test_resolve_moonlib_dll_uses_compatible_path_when_configured_x64_path_is_stale(
    tmp_path: Path,
) -> None:
    actual_dir = (
        tmp_path
        / "native"
        / "new_horizon"
        / "moonlib"
        / "bin"
        / "Debug"
        / "net9.0"
        / "linux-x64"
    )
    actual_dir.mkdir(parents=True, exist_ok=True)
    actual_dll = actual_dir / "moonlib.dll"
    actual_dll.write_bytes(b"")

    stale_cfg = NativeBootstrapConfig(
        moonlib_dll=tmp_path
        / "native"
        / "new_horizon"
        / "moonlib"
        / "bin"
        / "x64"
        / "Debug"
        / "net9.0"
        / "moonlib.dll"
    )

    selected = resolve_moonlib_dll(stale_cfg)
    assert selected == actual_dll.resolve()


def test_native_bundle_proj_and_gdal_data_override_existing_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tfm_dir = tmp_path / "net9.0"
    tfm_dir.mkdir(parents=True, exist_ok=True)
    moonlib_dll = tfm_dir / "moonlib.dll"
    moonlib_dll.write_bytes(b"")

    native_gdal_dir = tfm_dir / "gdal" / "data"
    native_proj_dir = tfm_dir / "gdal" / "share"
    native_gdal_dir.mkdir(parents=True, exist_ok=True)
    native_proj_dir.mkdir(parents=True, exist_ok=True)
    (native_proj_dir / "proj.db").write_bytes(b"")

    wrong_gdal_dir = tmp_path / "wrong" / "gdal"
    wrong_proj_dir = tmp_path / "wrong" / "proj"
    wrong_gdal_dir.mkdir(parents=True, exist_ok=True)
    wrong_proj_dir.mkdir(parents=True, exist_ok=True)
    (wrong_proj_dir / "proj.db").write_bytes(b"")

    monkeypatch.setenv("GDAL_DATA", str(wrong_gdal_dir))
    monkeypatch.setenv("PROJ_LIB", str(wrong_proj_dir))
    monkeypatch.setenv("PROJ_DATA", str(wrong_proj_dir))

    native_bootstrap._configure_native_dll_search_paths(  # type: ignore[attr-defined]
        moonlib_dll,
        NativeBootstrapConfig(moonlib_dll=moonlib_dll),
    )

    assert os.environ["GDAL_DATA"] == str(native_gdal_dir.resolve())
    assert os.environ["PROJ_LIB"] == str(native_proj_dir.resolve())
    assert os.environ["PROJ_DATA"] == str(native_proj_dir.resolve())
