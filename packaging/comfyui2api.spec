# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd().resolve()
SRC = ROOT / "src"


def data_files():
    items = []
    webui_dist = SRC / "comfyui2api" / "webui_dist"
    if webui_dist.exists():
        items.append((str(webui_dist), "comfyui2api/webui_dist"))
    for name in ("README.md", ".env.example"):
        path = ROOT / name
        if path.exists():
            items.append((str(path), "."))
    return items


a = Analysis(
    [
        str(SRC / "comfyui2api" / "desktop_entry.py"),
        str(SRC / "comfyui2api" / "cli_entry.py"),
    ],
    pathex=[str(SRC)],
    binaries=[],
    datas=data_files(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

runtime_hooks = [script for script in a.scripts if script[0].startswith("pyi_rth_")]
scripts_by_name = {script[0]: script for script in a.scripts}

desktop_exe = EXE(
    pyz,
    runtime_hooks + [scripts_by_name["desktop_entry"]],
    [],
    exclude_binaries=True,
    name="comfyui2api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

cli_exe = EXE(
    pyz,
    runtime_hooks + [scripts_by_name["cli_entry"]],
    [],
    exclude_binaries=True,
    name="comfyui2api-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    desktop_exe,
    cli_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="comfyui2api",
)
