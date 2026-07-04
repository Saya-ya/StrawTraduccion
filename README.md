# Traducción de Strawberry Panic! (PS2) — Guía del proceso

## Aviso Importante y Legal

* **Traducción 100% Gratuita:** Este parche es un proyecto hecho de fans para fans y su distribución es **completamente gratuita**. Queda estrictamente prohibida su venta o comercialización. Si pagaste por esta traducción o la descargaste de un sitio de pago, te han estafado.
* **Requiere ISO Original:** Para utilizar estas herramientas y aplicar el parche, es requisito indispensable que utilices una copia de seguridad (ISO) extraída de **tu propio juego original**. Este proyecto **NO** incluye, distribuye ni enlaza a ROMs o ISOs con derechos de autor. Por favor, apoya a los desarrolladores originales; en caso de que una versión traducida oficial por parte de la compañía sea licenciada en tu país, favor de borrar esta versión y adquirir la original.

---

## Arquitectura del juego

El juego guarda los textos en **dos lugares distintos**:

| Archivo | Qué contiene | Encoding | ¿Comprimido? |
|---------|-------------|----------|:---:|
| `SLPS_256.11` (ELF) | Menús, sistema, descripciones de personajes | **Shift-JIS** | **NO** |
| `Data.bin` (scripts LZ77) | Diálogos del juego (escenas) | **UTF-16LE** | **SÍ (LZSS)** |

`Data.bin` es un archive con **27,411 archivos** internos. De esos, **997** están comprimidos con LZSS. **58** son scripts de diálogo (`SCRIPT_DIALOGUE`, tipo `0x020000XX`), 48 con pointer table (Variante B) y 8 sin ella (Variante A). El resto son audio (SS2 ADPCM) y texturas (TIM2).

### Estructura de scripts SCRIPT_DIALOGUE

Cada script `.dec` descomprimido tiene:

| Región | Offset | Descripción |
|--------|--------|-------------|
| Header | 0x00-0x1F | 8 × u32: `type`, 3×reserved, `bytecode_ptr` (0x2010), `count` |
| Gap area | 0x20-0x200F | Variante A: todo ceros. Variante B: pointer table (entradas de 16 bytes = `[ptr:u32, count:u32, 0, 0]`) |
| Bytecode | 0x2010+ | Opcodes + bloques de texto UTF-16LE intercalados |

Cada **bloque de texto** tiene firma exacta de 24 bytes:
```
[8 ceros] [opcode 0x03] [param 0x0100] [8 ceros] [TEXTO UTF-16LE] [00 00] [padding]
```

Los **pointer entries** de la Variante B definen secciones narrativas que el motor del juego usa para indexar escenas. El orden en el bytecode es lineal y coincide con el orden de ejecución dentro de cada sección.

---

## Requisitos

- Python 3 instalado
- Tu ISO original del juego (extraída de tu propio disco)
- La carpeta `StrawTraduccion` (este proyecto)
- PCSX2 para probar

### Paso 1 — Preparar los archivos (una sola vez)

Abre la ISO con WinRAR o 7-Zip y extrae estos archivos a la carpeta `originales/`:
- `Data.bin`
- `SLPS_256.11`

Después ejecuta en la terminal:

```bash
python tools/extract_all.py --type lz77   # solo la primera vez
python tools/dialogue_order.py
```

Esto extrae los 997 scripts LZ77 y genera `textos/dialogo.csv` con los textos enriquecidos con secciones y orden narrativo (~54,000 líneas de diálogo).

### Paso 2 — Traducir en la Web App

1. Inicia el servidor web:
   ```bash
   python run_webapp.py
   ```
2. Abre `http://127.0.0.1:8080`
3. Ve a **Importar** y haz clic para cargar los textos en la base de datos
4. Usa las pestañas **Scripts** y **Buscar** para traducir:
   - Los scripts ahora muestran **secciones numeradas** — cada sección es una escena narrativa completa
   - Haz clic en cualquier texto para abrir el editor inline (guarda con `Ctrl+Enter`)
   - El sistema muestra si la traducción cabe (`🟢 cabe`, `🟡 ajustado`, `🔴 no cabe`)

### Paso 3 — Build y generar la ISO

Desde la pestaña **Build** → **Ejecutar Build Completo**. El sistema:
1. Exporta las traducciones de la DB a CSV
2. Reconstruye los scripts `.dec` con el modo `local-slack`
3. Reconstruye la ISO con `Data.bin` modificado
4. Aplica traducciones al ELF y lo inyecta en la ISO

La ISO traducida queda en `work/Strawberry_translated.iso`.

### Paso 4 — Probar en PCSX2

Copia `Replacement/adcbf16a55dddc9c-aafc910d2a31cd93-00002214.png` a la carpeta de texturas de PCSX2 (`textures/SLPS-25611/`). **Inicia el juego desde cero** (no cargues savestates — la RAM tendría los datos viejos).

---

## Mejoras implementadas

### v3 — Limpieza de código (2026-07-03)

- **6 herramientas legacy movidas a `docs/legado/`:** `parse_archive.py`, `split_csv.py`, `searcher.py`, `search_decompressed.py`, `extract_ram.py`, `pine_test.py`. No son usadas por el pipeline activo. `extract_all.py` permanece en `tools/` como paso de setup inicial.
- **`extract_dialogue.py` simplificado:** La extracción de scripts LZ77 fue reemplazada por `dialogue_order.py`. Solo queda la extracción de ELF (`--elf`).
- **Bug fix en `builder.py`:** `run_build_iso()` no existía como función (su cuerpo estaba como código muerto después de un `return` en `run_apply_translation`). Se extrajo a su propia función.
- **Import no usado:** `BackgroundTasks` removido de `routers/import_.py`.

### v2 — Sistema de orden narrativo

### Sistema de orden narrativo

| Herramienta | Archivo | Función |
|-------------|---------|---------|
| Detector de bloques de texto | `tools/dialogue_order.py` | Extrae textos por firma exacta de opcode 0x03 (0 falsos positivos) |
| Parser de pointer table | `tools/dialogue_order.py` | Agrupa textos en secciones narrativas (Variante B) |
| Detector de escenas | `tools/dialogue_order.py` | Marcador de continuación `0x06` para Variante A |
| CSV enriquecido | `textos/dialogo.csv` | Nuevas columnas: `section`, `section_order` |
| JSON estructural | `work/dialogue_order.json` | Árbol completo de scripts → secciones → textos |

### Filtro de ELF mejorado

| Filtro | Descripción |
|--------|-------------|
| `is_valid_elf_text()` | >=4 caracteres JP consecutivos, >50% ratio JP, contiene hiragana o es todo katakana |
| Resultado | 240 textos reales extraídos (vs 1,125 con el método antiguo — 0% basura) |

### Webapp actualizada

| Cambio | Archivo |
|--------|---------|
| Modelo DB con `section_id`, `section_order`, `variant` (A/B) | `webapp/database.py` |
| Import pipeline usa `dialogue_order.py` + ELF con filtro mejorado | `webapp/services/import_service.py` |
| Navegación por secciones en vista de script | `webapp/routers/scripts.py`, `script_detail.html` |
| Búsqueda con links a la sección/página correcta | `webapp/routers/tools.py`, `search_results.html` |
| Highlight redirect automático al entrar a un script | `webapp/routers/scripts.py` |
| Builder incluye paso de `apply_translation.py` para ELF | `webapp/services/builder.py` |
| Timeouts ajustados: rebuild 300s, pipeline 2h | `webapp/config.py` |
| CSV splitter con modo `--by-section` | `docs/legado/split_csv.py` |

### Correcciones de bugs

| Bug | Fix |
|-----|-----|
| Textos basura `ｄ$-0` del ELF (1,170 falsos positivos) | Filtro `is_valid_elf_text()` + eliminados del pipeline |
| Búsqueda llevaba a página equivocada (orden viejo por byte_offset) | Paginación recalcula posición por `section_order` |
| Highlight no encontraba el texto en la página actual | Redirect automático a `?section=X&page=Y&highlight=ID` |
| ELF no se inyectaba (`SLPS_256.11_translated` no existía) | Añadido `apply_translation.py` al pipeline del builder |
| `build_worker.py` timeout a 60s (script 8007 tarda ~2m15s) | Subido a 300s por script, pipeline total a 2h |
| `º` (ordinal) y `—` (em dash) mostraban `?` negro en el juego | Reemplazados por `o` y `-` en todas las traducciones |

### Estado de la traducción

| Fuente | Traducido | Total |
|--------|-----------|-------|
| 8007 — Historia principal (Nagisa x Shizuma) | 2,349 | 2,349 (100%) |
| 7461 — Narración de apertura | 40 | 40 |
| 8006 — Narración de apertura (dup) | 40 | 40 |
| ELF — Menús, descripciones, modos | 23 | 240 |
| **Total textos traducidos** | **2,475** | — |

Los 53 scripts restantes (~51,600 textos) contienen diálogos de otras rutas (modo hermano, otras protagonistas, escenas alternativas) y están pendientes de traducción.

---

## Guía técnica (detalles de bajo nivel)

### Método 1: Parcheo directo (`apply_translation.py`)

**Usar cuando:** el texto está en el ELF, o en un script LZ77 donde **todos** los bytes a modificar son LITERAL y la traducción cabe en el espacio original.

```bash
python traduccion_tools/apply_translation.py textos/dialogo.csv
```

### Método 2: Recompresión con rebuild (`patch_dec.py --rebuild`)

**Usar cuando:** necesitas reemplazar textos en scripts SCRIPT_DIALOGUE. Este es el método principal.

```bash
cp originales/Data.bin work/Data_patched.bin
python tools/patch_dec.py --id 8007 --rebuild --csv textos/dialogo.csv --verify
python traduccion_tools/build_iso.py
```

### Método 3: Extracción con orden narrativo (`dialogue_order.py`)

```bash
# Generar CSV enriquecido con secciones y orden
python tools/dialogue_order.py

# Solo un script
python tools/dialogue_order.py --id 8007

# Diagnóstico: comparar con CSV viejo, reportar huérfanos
python tools/dialogue_order.py --diagnose

# Exportar JSON estructural
python tools/dialogue_order.py --json analysis.json

# Resumen por script
python tools/dialogue_order.py --summary
```

---

## Flujo de trabajo completo

```
┌──────────────────────────────────────────────────────────────┐
│ 1. EXTRACCIÓN (una sola vez)                                 │
│                                                              │
│   python tools/extract_all.py --type lz77                    │
│   python tools/dialogue_order.py                           │
│                                                              │
│   → 997 .dec en work/scripts_extraidos/                      │
│   → textos/dialogo.csv con secciones y orden                 │
│   → work/dialogue_order.json                                 │
├──────────────────────────────────────────────────────────────┤
│ 2. TRADUCCIÓN (VÍA WEB APP)                                  │
│                                                              │
│   python run_webapp.py                                       │
│                                                              │
│   → http://127.0.0.1:8080                                    │
│   → Importar para cargar en la DB                            │
│   → Scripts: navegar por secciones, editar inline            │
│   → Cada sección = una escena narrativa                      │
├──────────────────────────────────────────────────────────────┤
│ 3. BUILD DE ISO (VÍA WEB APP)                                │
│                                                              │
│   → Build → Ejecutar Build Completo                          │
│                                                              │
│   El pipeline:                                               │
│   - export_csv_for_build()                                   │
│   - patch_dec.py --rebuild para cada script con traducciones │
│   - apply_translation.py → SLPS_256.11_translated            │
│   - build_iso.py → Strawberry_translated.iso                 │
│   - inject_elf.py                                            │
│                                                              │
│   → work/Strawberry_translated.iso                           │
└──────────────────────────────────────────────────────────────┘
```

---

## Estructura del proyecto

```
StrawTraduccion/
├── originales/                     # Archivos originales (NO distribuidos)
│   ├── Data.bin
│   ├── SLPS_256.11
│   └── Strawberry_patched.iso
├── work/                           # Archivos de trabajo (regenerables)
│   ├── scripts_extraidos/          # .dec extraídos (997 scripts)
│   ├── Data_patched.bin
│   ├── dialogue_order.json         # Orden narrativo (JSON estructural)
│   ├── SLPS_256.11_translated
│   └── Strawberry_translated.iso
├── tools/                          # Pipeline activo: LZ77, compresión, parcheo, extracción
│   ├── datafat.py                  # Parseo canónico de la FAT de Data.bin
│   ├── lz77.py                     # Decompresor/compresor LZSS de PS2
│   ├── script_rebuilder.py         # Rebuilder de .dec (modo local-slack)
│   ├── dialogue_order.py           # Extracción por firma de opcode + secciones
│   ├── patch_compressed.py         # Traza LZSS (trace_decompression)
│   ├── patch_dec.py                # Pipeline: reconstruye, recomprime e inyecta
│   ├── extract_all.py              # Extrae archivos .dec de Data.bin (setup inicial)
│   └── glyph_map.py                # Mapa de caracteres español → cirílico
├── traduccion_tools/               # Alto nivel: extracción ELF, build ISO
│   ├── extract_dialogue.py         # Extrae textos ELF (Shift-JIS) con filtro estricto
│   ├── apply_translation.py        # Aplica traducciones al ELF vía parcheo directo
│   ├── build_iso.py                # Reconstruye ISO con Data.bin modificado
│   └── inject_elf.py               # Inyecta ELF traducido en la ISO
├── webapp/                         # FastAPI + SQLite + FTS5 + HTMX
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── routers/
│   │   ├── scripts.py              # Navegación por secciones + highlight redirect
│   │   ├── texts.py                # Editor inline con fit status
│   │   ├── import_.py
│   │   ├── build.py
│   │   └── tools.py                # Dashboard, búsqueda FTS5, delegación
│   ├── services/
│   │   ├── builder.py              # Pipeline completo: export → rebuild → ISO → ELF
│   │   ├── import_service.py       # Usa dialogue_order.py + extract_dialogue.py --elf
│   │   ├── fit_checker.py
│   │   ├── capacity.py
│   │   └── build_lock.py
│   └── templates/
│       ├── base.html
│       ├── script_detail.html
│       ├── scripts_list.html
│       ├── search.html
│       └── components/
│           └── search_results.html
├── docs/
│   └── legado/                     # Herramientas legacy (no usadas por el pipeline activo)
│       ├── parse_archive.py        # Análisis de la FAT de Data.bin
│       ├── split_csv.py            # Divide CSV por script o sección
│       ├── searcher.py             # Búsqueda CLI en CSVs
│       ├── search_decompressed.py  # Búsqueda binaria en .dec
│       ├── extract_ram.py          # Extrae RAM de savestates PCSX2
│       └── pine_test.py            # Diagnóstico de API PINE de PCSX2
├── textos/
│   ├── dialogo.csv                 # CSV enriquecido
│   └── por_script/                 # CSVs divididos por script
├── Replacement/                    # Texturas de fuente (PCSX2)
├── build_worker.py
├── run_webapp.py
└── README.md
```

---

## El mapeo de fuente (español → cirílico)

El juego no tiene `á`, `é`, `ñ` en su fuente original. Para resolverlo, se reusan glifos de **caracteres cirílicos** que sí existen en la fuente del juego, reemplazando sus texturas vía PCSX2.

La tabla de sustitución vive en `tools/glyph_map.py`:

```python
SPANISH_TO_GLYPH_UTF16 = {
    'á': '\u0413',  # Г → se reemplazó su glifo por una á
    'é': '\u0414',  # Д → glifo reemplazado por é
    'í': '\u0415',  # Е → glifo reemplazado por í
    'ó': '\u0416',  # Ж → glifo reemplazado por ó
    'ú': '\u0417',  # З → glifo reemplazado por ú
    'ñ': '\u0418',  # И → glifo reemplazado por ñ
    ...
}
```

El traductor **escribe español normal** (`á`, `é`, `ñ`). El script de parcheo hace la sustitución automáticamente. **Importante**: caracteres como `º` (ordinal) y `—` (em dash) no están en la fuente — se reemplazan por `o` y `-`.

---

## Notas técnicas

### Fix del header LZ77 (2026-06-28)
El decompressor nativo del PS2 procesa el header LZ77 con 12 bytes (no 16). Nuestro Python original usaba 16 bytes y empezaba a descomprimir 4 bytes después que el PS2, produciendo una salida diferente (6,905 bytes de diferencia) y causando pantalla negra. **Fix:** header de 12 bytes.

### Fix de la FAT (2026-06-29)
El `size_field` de la fila *i* NO es el tamaño de ese archivo: es el tamaño del archivo de la fila **anterior**. El tamaño real está en el `size_field` de la fila **i+1**. **Fix:** módulo `datafat.py` centraliza el parseo correcto.

### Fix de la pantalla negra (2026-06-28)
El PS2 nativo inicializa la ventana LZ77 con `0x00` en sus 4096 bytes. El decompresor de Python también lo hace ahora.

---

## Limitaciones actuales

1. **Solo 58 scripts son SCRIPT_DIALOGUE.** Los ~939 scripts restantes (TEXT_HEAVY, DATA_OR_TABLE, TIM2_TEXTURE) no están soportados por el rebuilder y requieren edición manual del `.dec`.
2. **Fuente:** Requiere texturas de reemplazo en PCSX2 para caracteres españoles (áéíóúñ¡¿). Ver `Replacement/`.
3. **Traducciones que no caben en padding:** Si una traducción excede el padding local (~150-230 bytes), el rebuilder la marca `needs_shift` y no la aplica.
4. **Branching detectado pero no aplicado en rebuild:** `dialogue_order.py` detecta branching (ramas narrativas alternativas) pero el rebuilder no lo maneja al reconstruir.
5. **ELF parcial:** Solo 23/240 textos del ELF están traducidos (menús, descripciones, modos de juego).
