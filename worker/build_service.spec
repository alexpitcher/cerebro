# -*- mode: python ; coding: utf-8 -*-

import pathlib

spec_path = pathlib.Path(globals().get("__file__", "worker/build_service.spec")).resolve()
project_dir = spec_path.parent
service_entry = project_dir / "service_app.py"

datas = [
    (str(project_dir / "config.json"), ""),
    (str(project_dir / "resources"), "resources"),
]

a = Analysis(
    [str(service_entry)],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=["win32timezone"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="CerebroWorkerService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_dir / "icon.ico"),
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
