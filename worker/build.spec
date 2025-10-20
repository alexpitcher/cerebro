# -*- mode: python ; coding: utf-8 -*-

import pathlib

spec_path = pathlib.Path(globals().get("__file__", "worker/build.spec")).resolve()
project_dir = spec_path.parent
gui_entry = project_dir / "gui_worker.py"
datas = [
    (str(project_dir / "config.json"), "."),
    (str(project_dir / "resources"), "resources"),
    (str(project_dir / "gui_requirements.txt"), "."),
    (str(project_dir / "resources" / "icons"), "resources/icons"),
]

a = Analysis(
    [str(gui_entry)],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name="CerebroWorker",
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
