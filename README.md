# StrawTraduccion — Strawberry Panic! (PS2) Translation System

[English](#english) | [Español](#español)

---

## English

A complete translation toolchain and web-based management system for the PS2 visual novel *"Strawberry Panic!"*. Extracts, organizes, translates, and rebuilds game text into a fully patched, playable ISO.

### Legal Notice

* **100% Free:** This is a fan project, made by fans for fans. Distribution is completely free. Selling or commercializing this patch is strictly prohibited.
* **Requires Original ISO:** You must use a backup (ISO) extracted from your own original game disc. This project does NOT include, distribute, or link to copyrighted ROMs or ISOs.

---

### Quick Start

1. Extract `Data.bin` and `SLPS_256.11` from your ISO into the `originales/` folder
2. Start the web server:
   ```bash
   python run_webapp.py
   ```
3. Open `http://127.0.0.1:8080`
4. Go to **Import** and click **Extract and Import** to load texts into the database
5. Use the **Scripts** and **Search** tabs to translate — click any text for inline editing (`Ctrl+Enter` to save)
6. Go to **Build** and click **Build ISO** to generate the patched ISO at `work/Strawberry_translated.iso`
7. Copy `Replacement/*.png` to PCSX2's texture folder (`textures/SLPS-25611/`), boot the game fresh (no savestates)

---

### Interface Language & Target Translation Language

The webapp supports multiple UI languages. A ⚙ icon in the top-right navbar opens the **Settings** page, where you can independently configure:

| Setting | Options | Effect |
|---|---|---|
| **UI Language** | ES (Spanish) / EN (English) | Changes all navigation, buttons, tooltips, and status messages |
| **Target Language** | Spanish / English / Custom | Determines which glyph substitution map is applied when patching |

All preferences persist in the SQLite database and survive server restarts.

---

### Glyph Mapping System

The game's Japanese font lacks accented characters (`á`, `é`, `ñ`, etc.). To work around this, the system substitutes them with **Cyrillic characters** that do exist in the font (`Г`, `Д`, `И`, etc.), then replaces the Cyrillic glyph textures via PCSX2's texture injection.

#### Spanish (default)

Hardcoded 16-entry map. The translator writes normal Spanish — the system handles everything automatically:

| Spanish char | Cyrillic glyph | PCSX2 texture replaces |
|---|---|---|
| á, Á | Г | Cyrillic Г → Spanish á/Á |
| é, É | Д | Cyrillic Д → Spanish é/É |
| í, Í | Е | Cyrillic Е → Spanish í/Í |
| ó, Ó | Ж | Cyrillic Ж → Spanish ó/Ó |
| ú, Ú, ü, Ü | З | Cyrillic З → Spanish ú/Ü |
| ñ | И | Cyrillic И → Spanish ñ |
| Ñ | Й | Cyrillic Й → Spanish Ñ |
| ¡ | К | Cyrillic К → Spanish ¡ |
| ¿ | Л | Cyrillic Л → Spanish ¿ |

#### English

No glyph mapping needed. English uses only ASCII characters present in the game's font. Selecting English as target language sets the glyph map to empty — all text is encoded as-is.

#### Custom (e.g. Polish, German, French)

For any language whose special characters fit in Latin-1 encoding, you can define a custom glyph map. The Settings page shows a table of **64 available Cyrillic glyphs** — assign each one to a character from your language. Example for Polish:

| Polish char | Cyrillic glyph |
|---|---|
| ą | Г |
| ć | Д |
| ę | Е |
| ł | Ж |
| ń | И |
| ó | К |
| ś | Л |
| ź | Й |
| ż | З |

The custom map is saved to the database and applied automatically during patching. You must provide your own PCSX2 texture replacements matching your assignments.

---

### How Translation Flows to the Patcher

1. Translator edits texts in the webapp → saved to `text_entries` table in `translation_manager.db`
2. Target language and glyph map are stored in the `settings` table
3. The standalone **StrawPatcher** (`.exe`) reads the `.db` file, detects `target_lang` from `settings`, loads the correct glyph map, and applies it when rebuilding scripts and ELF data
4. The patcher's GUI shows the active language and glyph mapping before starting

---

### System Architecture

| Layer | Technology |
|---|---|
| Backend | Python 3, FastAPI, Uvicorn |
| Database | SQLite + SQLAlchemy ORM + FTS5 full-text search |
| Frontend | Jinja2 templates + HTMX + Tailwind CSS (CDN) |
| I18N | Python dict-based string tables (ES/EN) |
| CLI tools | Pure Python stdlib + `struct` for binary I/O |
| Compression | PS2-native LZSS (4096-byte window, 12-byte header) |

The game stores text in two locations:

| File | Content | Encoding | Compressed? |
|---|---|---|---|
| `SLPS_256.11` (ELF) | Menus, system, character descriptions | Shift-JIS | No |
| `Data.bin` (LZ77 scripts) | In-game dialogue scenes | UTF-16LE | Yes (LZSS) |

`Data.bin` contains **27,411 files** internally. **997** are LZSS-compressed; **58** are dialogue scripts with `0x03` opcode signature. The system handles all extraction, section-based ordering, and conservative rebuilding (local-slack mode — replaces text within existing zero-padding without moving pointers).

---

### Project Structure

```
StrawTraduccion/
├── originales/                     # Original game files (NOT distributed)
├── work/                           # Working files (regenerable)
├── tools/                          # Core pipeline: LZ77, FAT, rebuild, extraction
├── traduccion_tools/               # High-level: ELF extraction, ISO building
├── webapp/                         # FastAPI + SQLite + FTS5 + HTMX
│   ├── main.py                     # App entry point, I18nMiddleware
│   ├── i18n/                       # UI string tables (es.py, en.py)
│   ├── routers/                    # scripts, texts, import, build, tools, settings
│   ├── services/                   # builder, import_service, fit_checker, settings_service
│   └── templates/                  # Jinja2 + Tailwind + HTMX templates
├── textos/                         # Generated CSVs
├── Replacement/                    # PCSX2 font texture replacements
├── docs/legado/                    # Legacy utilities
├── tests/                          # Automated tests
├── build_worker.py                 # Standalone build worker
├── run_webapp.py                   # Uvicorn launcher
└── README.md
```

---

## Español

Un sistema completo de traducción con interfaz web para la novela visual de PS2 *"Strawberry Panic!"*. Extrae, organiza, traduce y reconstruye los textos del juego generando una ISO totalmente parcheada y jugable.

### Aviso Legal

* **100% Gratis:** Este es un proyecto de fans para fans. Su distribución es completamente gratuita. Queda estrictamente prohibida su venta o comercialización.
* **Requiere ISO Original:** Debes usar una copia de seguridad (ISO) extraída de tu propio disco original. Este proyecto NO incluye, distribuye ni enlaza ROMs o ISOs con derechos de autor.

---

### Inicio Rápido

1. Extrae `Data.bin` y `SLPS_256.11` de tu ISO a la carpeta `originales/`
2. Inicia el servidor web:
   ```bash
   python run_webapp.py
   ```
3. Abre `http://127.0.0.1:8080`
4. Ve a **Importar** y haz clic en **Extraer e Importar** para cargar los textos
5. Usa las pestañas **Scripts** y **Buscar** para traducir — clic en cualquier texto para editar (`Ctrl+Enter` para guardar)
6. Ve a **Build** y haz clic en **Construir ISO** para generar la ISO parcheada en `work/Strawberry_translated.iso`
7. Copia `Replacement/*.png` a la carpeta de texturas de PCSX2 (`textures/SLPS-25611/`), inicia el juego desde cero (sin savestates)

---

### Idioma de la Interfaz e Idioma de Traducción

La webapp soporta múltiples idiomas de interfaz. El icono ⚙ en la barra superior abre la página de **Configuración**, donde puedes ajustar de forma independiente:

| Configuración | Opciones | Efecto |
|---|---|---|
| **Idioma de la interfaz** | ES (Español) / EN (Inglés) | Cambia toda la navegación, botones, tooltips y mensajes |
| **Idioma de traducción** | Español / Inglés / Personalizado | Determina qué mapa de sustitución de glifos se aplica al parchear |

Todas las preferencias se guardan en la base de datos SQLite y persisten entre reinicios.

---

### Sistema de Mapeo de Glifos

La fuente japonesa del juego no tiene caracteres acentuados (`á`, `é`, `ñ`, etc.). Para resolverlo, el sistema los sustituye por **caracteres cirílicos** que sí existen en la fuente (`Г`, `Д`, `И`, etc.), y luego reemplaza las texturas de esos glifos mediante inyección de texturas en PCSX2.

#### Español (por defecto)

Mapa fijo de 16 entradas. El traductor escribe español normal — el sistema lo convierte automáticamente:

| Carácter español | Glifo cirílico | La textura de PCSX2 reemplaza |
|---|---|---|
| á, Á | Г | Glifo Г → á/Á española |
| é, É | Д | Glifo Д → é/É española |
| í, Í | Е | Glifo Е → í/Í española |
| ó, Ó | Ж | Glifo Ж → ó/Ó española |
| ú, Ú, ü, Ü | З | Glifo З → ú/Ü española |
| ñ | И | Glifo И → ñ española |
| Ñ | Й | Glifo Й → Ñ española |
| ¡ | К | Glifo К → ¡ española |
| ¿ | Л | Glifo Л → ¿ española |

#### Inglés

No necesita mapeo de glifos. El inglés usa solo caracteres ASCII presentes en la fuente del juego. Al seleccionar inglés como idioma de destino, el mapa de glifos queda vacío — todo el texto se codifica tal cual.

#### Personalizado (ej. polaco, alemán, francés)

Para cualquier idioma cuyos caracteres especiales quepan en codificación Latin-1, puedes definir un mapa de glifos personalizado. La página de Configuración muestra una tabla de **64 glifos cirílicos disponibles** — asigna cada uno a un carácter de tu idioma. Ejemplo para polaco:

| Carácter polaco | Glifo cirílico |
|---|---|
| ą | Г |
| ć | Д |
| ę | Е |
| ł | Ж |
| ń | И |
| ó | К |
| ś | Л |
| ź | Й |
| ż | З |

El mapa personalizado se guarda en la base de datos y se aplica automáticamente al parchear. Debes proporcionar tus propios reemplazos de texturas en PCSX2 que coincidan con tus asignaciones.

---

### Cómo Fluye la Traducción al Parcheador

1. El traductor edita textos en la webapp → guardados en la tabla `text_entries` de `translation_manager.db`
2. El idioma de destino y el mapa de glifos se almacenan en la tabla `settings`
3. El **StrawPatcher** independiente (`.exe`) lee el `.db`, detecta `target_lang` desde `settings`, carga el mapa de glifos correcto y lo aplica al reconstruir scripts y datos del ELF
4. La interfaz del parcheador muestra el idioma activo y el mapeo antes de iniciar

---

### Arquitectura del Sistema

| Capa | Tecnología |
|---|---|
| Backend | Python 3, FastAPI, Uvicorn |
| Base de datos | SQLite + SQLAlchemy ORM + FTS5 búsqueda textual |
| Frontend | Plantillas Jinja2 + HTMX + Tailwind CSS (CDN) |
| I18N | Tablas de strings basadas en dicts de Python (ES/EN) |
| Herramientas CLI | Python stdlib puro + `struct` para I/O binario |
| Compresión | LZSS nativo de PS2 (ventana 4096 bytes, header 12 bytes) |

El juego guarda los textos en dos lugares:

| Archivo | Contenido | Encoding | ¿Comprimido? |
|---|---|---|---|
| `SLPS_256.11` (ELF) | Menús, sistema, descripciones | Shift-JIS | No |
| `Data.bin` (scripts LZ77) | Diálogos del juego | UTF-16LE | Sí (LZSS) |

`Data.bin` contiene **27,411 archivos** internos. **997** están comprimidos con LZSS; **58** son scripts de diálogo con firma de opcode `0x03`. El sistema maneja toda la extracción, ordenamiento por secciones narrativas, y reconstrucción conservadora (modo local-slack — reemplaza texto dentro del padding de ceros existente sin mover punteros).

---

### Estructura del Proyecto

```
StrawTraduccion/
├── originales/                     # Archivos originales (NO distribuidos)
├── work/                           # Archivos de trabajo (regenerables)
├── tools/                          # Pipeline principal: LZ77, FAT, rebuild, extracción
├── traduccion_tools/               # Alto nivel: extracción ELF, build ISO
├── webapp/                         # FastAPI + SQLite + FTS5 + HTMX
│   ├── main.py                     # Punto de entrada, I18nMiddleware
│   ├── i18n/                       # Tablas de strings de UI (es.py, en.py)
│   ├── routers/                    # scripts, texts, import, build, tools, settings
│   ├── services/                   # builder, import_service, fit_checker, settings_service
│   └── templates/                  # Plantillas Jinja2 + Tailwind + HTMX
├── textos/                         # CSVs generados
├── Replacement/                    # Reemplazos de texturas para PCSX2
├── docs/legado/                    # Utilidades legacy
├── tests/                          # Tests automatizados
├── build_worker.py                 # Worker de build independiente
├── run_webapp.py                   # Lanzador Uvicorn
└── README.md
```

---

## Technical Notes

### Line break system (`\r\n`)
The game uses `\r\n` (CRLF) for line breaks inside dialogue boxes. Each `\r\n` takes 4 bytes in UTF-16LE. The web editor auto-inserts proportional breaks based on the Japanese original, searching for nearby spaces or punctuation to avoid splitting words.

### `@` click-pause markers
The `@` character (and its fullwidth variant `＠`) is a click-pause marker in the game engine: text stops and waits for the player to press a button. It must be preserved at the same positions in translations.

### LZ77 header fix
The PS2 native decompressor processes the LZ77 header as 12 bytes (not 16). Using 16 bytes causes a 6,905-byte difference in decompressed output and results in a black screen.

### FAT quirk
The `size_field` of row *i* is NOT the size of that file — it's the size of the file in the **previous** row. The actual size is in the `size_field` of row **i+1**. Module `datafat.py` centralizes correct parsing.

### Current limitations
- Only 58 scripts are SCRIPT_DIALOGUE. Remaining ~939 require manual `.dec` editing.
- Requires PCSX2 texture replacement for non-ASCII characters.
- Translations exceeding local padding (~150-230 bytes) are flagged `needs_shift` and skipped.
- Branching detection exists but is not applied during rebuild.
- ELF translation is partial (menus, descriptions, game modes).
