# PyInstaller spec — builds a single-file Windows exe for Job Hunter.
#
#   pyinstaller job.spec
#
# Bundles the Jinja templates and static assets (which aren't Python imports),
# and pulls in runtime-imported modules PyInstaller can't detect statically.

from PyInstaller.utils.hooks import collect_submodules

datas = [
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
    (".env.example", "."),
]

hiddenimports = []
# uvicorn loads its protocol/loop implementations dynamically.
hiddenimports += collect_submodules("uvicorn")
# these are imported lazily / via strings in various libs
hiddenimports += [
    "anyio._backends._asyncio",
    "app.main",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="job",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,       # keep a console so users see the URL / can close to stop
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
