from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Rutas del proyecto
ORIGINALES = PROJECT_ROOT / 'originales'
WORK = PROJECT_ROOT / 'work'
TOOLS = PROJECT_ROOT / 'tools'
TRAD_TOOLS = PROJECT_ROOT / 'traduccion_tools'
TEXTOS = PROJECT_ROOT / 'textos'
TEXTURAS = PROJECT_ROOT / 'texturas'
REPLACEMENT = PROJECT_ROOT / 'Replacement'
WORK_TEXTURES = PROJECT_ROOT / 'work_texturas'
TEXTURE_CATALOG = WORK_TEXTURES / 'output' / 'all_textures'
TEXTURE_CATALOG_PNG = TEXTURE_CATALOG / 'png'
TEMPLATES = str(Path(__file__).parent / 'templates')

# Base de datos
DB_PATH = WORK / 'translation_manager.db'
DB_URL = f"sqlite:///{DB_PATH}"

# Build
BUILD_STATE_FILE = WORK / 'build_state.json'
BUILD_LOCK_FILE = WORK / 'build.lock'
BUILD_TEMP_DIR = WORK / 'build_temp'

# Subprocess timeouts (segundos)
TIMEOUT_EXTRACT = 600       # extract_dialogue.py full (997 scripts)
TIMEOUT_REBUILD = 300       # patch_dec.py --rebuild (1 script, hasta 3 min para scripts grandes)
TIMEOUT_APPLY_ELF = 1800    # apply_translation.py (hasta 30 min con muchos textos)
TIMEOUT_BUILD_ISO = 600     # build_iso.py (parseo de ISO grande)
TIMEOUT_INJECT_ELF = 30     # inject_elf.py
TIMEOUT_PIPELINE = 7200     # pipeline completo (2h max)

# Server
HOST = "127.0.0.1"
PORT = 8080


def ensure_runtime_dirs() -> None:
    """Create all runtime/project data folders used by the web app."""
    for path in (
        ORIGINALES,
        WORK,
        BUILD_TEMP_DIR,
        TEXTOS,
        TEXTURAS,
        REPLACEMENT,
        WORK_TEXTURES,
        TEXTURE_CATALOG,
        TEXTURE_CATALOG_PNG,
    ):
        path.mkdir(parents=True, exist_ok=True)
