# Traducción de Strawberry Panic! (PS2) — Guía del proceso

## ⚠️ Aviso Importante y Legal

* **Traducción 100% Gratuita:** Este parche es un proyecto hecho de fans para fans y su distribución es **completamente gratuita**. Queda estrictamente prohibida su venta o comercialización. Si pagaste por esta traducción o la descargaste de un sitio de pago, te han estafado.
* **Requiere ISO Original:** Para utilizar estas herramientas y aplicar el parche, es requisito indispensable que utilices una copia de seguridad (ISO) extraída de **tu propio juego original**. Este proyecto **NO** incluye, distribuye ni enlaza a ROMs o ISOs con derechos de autor. Por favor, apoya a los desarrolladores originales; en caso de que una versión traducida oficial por parte de la compañía sea licenciada en tu país, favor de borrar esta versión y adquirir la original.

---

## Arquitectura del juego

El juego guarda los textos en **dos lugares distintos**:

| Archivo | Qué contiene | Encoding | ¿Comprimido? |
|---------|-------------|----------|:---:|
| `SLPS_256.11` (ELF) | Menús, sistema, descripciones de personajes | **Shift-JIS** | **NO** |
| `Data.bin` (scripts LZ77) | Diálogos del juego (escenas) | **UTF-16LE** | **SÍ (LZSS)** |

`Data.bin` es un archive con **27,411 archivos** internos. De esos, **997** están comprimidos con LZSS y contienen los scripts. El resto son audio (SS2 ADPCM) y texturas (TIM2).

---

## Cómo identificar el tipo de texto

Al abrir `textos/dialogo.csv`, la columna `source` te dice cómo tratarlo:

| source | Significado | Método |
|--------|-------------|--------|
| `ELF` | Texto sin comprimir (Shift-JIS en el ejecutable) | Parcheo directo |
| `SCRIPT` | Texto comprimido con LZSS (UTF-16LE en Data.bin) | Parcheo directo **o** recompresión |

---

## Guía para traductores

Si solo querés traducir textos sin meterte en detalles técnicos, seguí estos pasos:

### Requisitos

- Python 3 instalado en tu PC
- Tu ISO original del juego (extraída de tu propio disco)
- La carpeta `StrawTraduccion` (este proyecto)
- PCSX2 para probar

### Paso 1 — Preparar los archivos (una sola vez)

Abrí la ISO con WinRAR o 7-Zip y extraé estos archivos a la carpeta `originales/`:
- `Data.bin`
- `SLPS_256.11`

Después ejecutá en la terminal:

```bash
python tools/extract_all.py --type lz77
python traduccion_tools/extract_dialogue.py
python traduccion_tools/extract_dialogue.py --elf
```

Esto genera `textos/dialogo.csv` con ~75,000 líneas de diálogo extraídas.

### Paso 2 — Traducir

Abrí `textos/dialogo.csv` con **LibreOffice Calc** o **Excel**. Verás 5 columnas:

| source | file_id | offset | original_text | translated_text |
|---|---|---|---|---|
| SCRIPT | 7461 | 0x02048 | 桜の園の奧深くに | |
| SCRIPT | 7461 | 0x0205C | 汚れを知らない乙女たちが集う | |

Llená la columna `translated_text` con tu traducción al español. Escribí normalmente con tildes y eñes — las herramientas las convierten automáticamente a los glifos del juego.

Para buscar frases específicas:
```bash
python traduccion_tools/searcher.py "texto a buscar"
```

**Importante**: solo funcionan los textos con `source = SCRIPT` cuyos `file_id` estén en la lista de scripts soportados. Actualmente son **48 scripts** (IDs 7461, 8005-8007, 8010-8043, 8047, 8050-8063). Los textos `source = ELF` (menús) usan otro método.

### Paso 3 — Aplicar las traducciones y generar la ISO

```bash
# Copiar Data.bin virgen como base
cp originales/Data.bin work/Data_patched.bin

# Aplicar cada script traducido (repetir por cada file_id que tenga traducciones)
python tools/patch_dec.py --id 7461 --rebuild --csv textos/dialogo.csv --verify
python tools/patch_dec.py --id 8006 --rebuild --csv textos/dialogo.csv --verify

# Construir la ISO final
python traduccion_tools/build_iso.py
python traduccion_tools/inject_elf.py
```

La ISO traducida queda en `work/Strawberry_translated.iso`.

### Paso 4 — Probar en PCSX2

Copiá el archivo PNG de la carpeta `Replacement/` a la carpeta de texturas de PCSX2 (`textures/SLPS-25611/`). Esto es necesario para que los caracteres españoles (á, é, í, ó, ú, ñ, ¡, ¿) se vean correctamente.

### Si algo falla

- Si el comando dice `needs_shift`: tu traducción es demasiado larga para el espacio disponible. Acortala o dividila.
- Si no encuentra tu `file_id` en el CSV: verificá que exista en `work/scripts_extraidos/` y que sea de tipo `SCRIPT_DIALOGUE`.
- Si la ISO no arranca: volvé a copiar `originales/Data.bin` a `work/Data_patched.bin` y re-ejecutá los comandos de parcheo.

---

## Guía técnica (detalles de bajo nivel)

## Método 1: Parcheo directo (`apply_translation.py`)

**Usar cuando:** el texto está en el ELF, o en un script LZ77 donde **todos** los bytes a modificar son LITERAL y la traducción cabe en el espacio original.

Modifica bytes **dentro del stream original** sin cambiar su estructura. No recomprime.

**Limitaciones:**
- La traducción debe ocupar ≤ bytes que el original
- Si un byte es MATCH (referencia a datos anteriores), no se puede tocar (~15% de los casos)
- Si algún texto falla, hay que usar recompresión para **ese script entero**

```bash
python traduccion_tools/apply_translation.py textos/dialogo.csv
```

Salida típica:
```
=== Resultados ===
  Aplicadas OK:        10
  Saltadas (tamaño):   1    ← traducción más larga que el original
  Saltadas (MATCH):    6    ← bytes referenciados, no modificables
```

---

## Método 2: Recompresión manual (`patch_dec.py`)

**Usar cuando:** el script NO es de tipo SCRIPT_DIALOGUE, o necesitás forzar
compresión sin matches (`--all-literal`).

Flujo:
1. Modificas el texto **directamente en el `.dec`** (con editor hex o script)
2. Recomprime e inyecta en `Data.bin`

```bash
cp originales/Data.bin work/Data_patched.bin
python tools/patch_dec.py --id 7461 --dec work/scripts_extraidos/ID_07461.dec
```

Para máxima seguridad (evitar posibles bugs del compresor):
```bash
python tools/patch_dec.py --id 7461 --dec <archivo> --all-literal --verify
```

---

## Método 3: Script Rebuilder (`script_rebuilder.py`, modo `local-slack`)

**Usar cuando:** la traducción es más larga que el original japonés pero cabe en
el *padding* (zona de ceros) que sigue a cada texto dentro del `.dec`.

Este es el **método recomendado** para traducir scripts de diálogo. Solo funciona
con los 48 scripts tipo `SCRIPT_DIALOGUE` (ver `work/analysis/dec_inventory.json`).

Cada texto en estos scripts tiene esta forma:

```
[texto UTF-16LE] [00 00] [00 00 00 00 ... ] [siguiente bloque]
                  null       padding (~150-190 bytes)
```

El modo `local-slack`:
- Localiza el string null-terminated que contiene el `offset` del CSV.
- Reemplaza el texto (con el mapeo español→glifos del juego).
- Reescribe terminador + ceros **sin pasar del siguiente bloque**.
- **No mueve estructuras ni toca punteros** → no puede romper el bytecode.
- Si un texto no cabe en su padding local, lo marca `needs_shift` y **no** lo aplica.

Uso autónomo (genera un `.dec` reconstruido + reporte):

```bash
# Análisis ligero del .dec (header + strings detectados)
python tools/script_rebuilder.py --id 7461 --analyze

# Dry-run: valida el CSV y reporta sin escribir nada
python tools/script_rebuilder.py --id 7461 --csv textos/dialogo.csv --dry-run

# Reconstruir el .dec aplicando el CSV
python tools/script_rebuilder.py --id 7461 --csv textos/dialogo.csv \
    --out work/scripts_extraidos/ID_07461_rebuilt.dec
```

Uso integrado en el pipeline (reconstruye desde CSV, recomprime e inyecta):

```bash
python tools/patch_dec.py --id 7461 --rebuild --csv textos/dialogo.csv --verify
python herramientas_tools/build_iso.py
```

---

## Flujo de trabajo completo

```
┌──────────────────────────────────────────────────────────────┐
│ 1. EXTRACCIÓN (una sola vez)                                 │
│                                                              │
│   python tools/extract_all.py --type lz77                    │
│   → work/scripts_extraidos/ID_*.dec  (997 archivos)          │
│                                                              │
│   python traduccion_tools/extract_dialogue.py                │
│   python traduccion_tools/extract_dialogue.py --elf          │
│   → textos/dialogo.csv  (~75,000 textos)                     │
│                                                              │
│   Columnas: [source, file_id, offset, original, translated]  │
├──────────────────────────────────────────────────────────────┤
│ 2. TRADUCCIÓN (MANUAL)                                       │
│                                                              │
│   Editar la columna "translated_text" en el CSV              │
│   Ayuda: python traduccion_tools/searcher.py "texto"         │
│   ⚠️ Solo funcionan los scripts tipo SCRIPT_DIALOGUE (48)    │
│      cuyos textos caben en el padding local (~150-190 B)     │
├──────────────────────────────────────────────────────────────┤
│ 3. APLICACIÓN (recomendada: --rebuild)                       │
│                                                              │
│   # Método principal: Script Rebuilder (automático)          │
│   cp originales/Data.bin work/Data_patched.bin               │
│   python tools/patch_dec.py --id 7461 --rebuild --csv        │
│          textos/dialogo.csv --verify                         │
│   python tools/patch_dec.py --id 8006 --rebuild --csv        │
│          textos/dialogo.csv --verify                         │
│   (repetir para cada file_id con traducciones)               │
│                                                              │
│   # Fallback si --rebuild marca needs_shift:                 │
│   #   usar --all-literal para forzar sin matches             │
│   python tools/patch_dec.py --id <ID> --dec <archivo>        │
│          --all-literal --verify                              │
├──────────────────────────────────────────────────────────────┤
│ 4. CONSTRUCCIÓN DE ISO                                       │
│                                                              │
│   python traduccion_tools/build_iso.py                       │
│   python traduccion_tools/inject_elf.py                      │
│   → work/Strawberry_translated.iso                           │
└──────────────────────────────────────────────────────────────┘
```

---

## El mapeo de fuente (español → cirílico)

El juego no tiene `á`, `é`, `ñ` en su fuente original. Para resolverlo, se reusan glifos de **caracteres cirílicos** que sí existen en la fuente del juego, reemplazando sus texturas vía PCSX2.

La tabla de sustitución vive en `apply_translation.py`:

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

El traductor **escribe español normal** (`á`, `é`, `ñ`). El script de parcheo hace la sustitución automáticamente. El CSV es 100% legible, sin códigos raros.

---

## Estructura del proyecto

```
StrawTraduccion/
├── originales/                # Archivos originales (NO distribuidos)
│   ├── Data.bin
│   ├── SLPS_256.11
│   └── Strawberry_patched.iso
├── work/                      # Archivos de trabajo (regenerables)
│   ├── scripts_extraidos/     # .dec extraídos (997 scripts)
│   ├── Data_patched.bin
│   ├── SLPS_256.11_translated
│   └── Strawberry_translated.iso
├── tools/                     # Bajo nivel: LZ77, compresión, parcheo
│   ├── datafat.py              # Parseo canónico de la FAT de Data.bin
│   ├── lz77.py                 # Decompresor/compresor LZSS de PS2
│   ├── script_rebuilder.py     # Rebuilder de .dec (modo local-slack)
│   ├── patch_compressed.py     # Parcheo directo en stream LZSS
│   ├── patch_dec.py            # Pipeline: reconstruye, recomprime e inyecta
│   ├── extract_all.py          # Extrae archivos individuales de Data.bin
│   └── parse_archive.py        # Analiza la FAT de Data.bin
├── traduccion_tools/          # Alto nivel: extracción, traducción, ISO
│   ├── extract_dialogue.py    # Extrae textos JP → CSV (heurística)
│   ├── apply_translation.py   # Aplica CSV: parcheo directo (ambos tipos)
│   ├── build_iso.py           # Reconstruye ISO con Data.bin modificado
│   ├── inject_elf.py          # Inyecta ELF traducido en la ISO
│   └── searcher.py            # Busca frases en los CSVs
├── pine_*.py                  # Herramientas PINE (RAM patching en vivo)
├── extract_ram.py             # Extrae RAM de savestates PCSX2
├── textos/                    # CSVs de traducción
│   └── dialogo.csv            # 75,924 textos (SCRIPTS + ELF)
├── Replacement/               # Texturas de fuente (PCSX2)
└── README.md
```

---

## Nota técnica: El fix del header LZ77

El decompressor nativo del PS2 (código MIPS en el ELF) procesa el header LZ77 así:

```
[magic "LZ77":4] [decomp_size:4] [comp_size:4] [stream comprimido:N]
 ←── 12 bytes de header ──→ ←── stream empieza aquí ──→
```

El campo que llamábamos "metadata" (bytes 12-15) **no es un campo separado** — es el inicio del stream comprimido. Nuestro Python original usaba un header de 16 bytes y empezaba a descomprimir 4 bytes después que el PS2, produciendo una salida diferente (6,905 bytes de diferencia) y causando pantalla negra.

**Fix aplicado (2026-06-28):** header de 12 bytes, decompresor salta 12 bytes (no 16), compresor genera stream continuo desde byte 12.

---

## Nota técnica: El fix de la FAT (causa raíz de la corrupción al recomprimir)

La FAT de `Data.bin` (offset `0x8004`, registros de 12 bytes
`[id:u32, size_field:u32, offset:u32]`) tiene una particularidad clave:

- El `offset` de la fila *i* SÍ corresponde al archivo con ese `id`.
- El `size_field` de la fila *i* **NO es el tamaño de ese archivo**: es el tamaño
  del archivo de la fila **anterior**. El tamaño real que el juego usa para leer
  el archivo *i* del CD está en el `size_field` de la fila **i+1**.

Verificado contra el binario: para los 997 scripts LZ77, `12 + comp_size` (del
header LZ77) coincide con el `size_field` de la fila siguiente **997/997 veces**,
nunca con la propia fila.

**Bug que causaba la corrupción:** al recomprimir, el stream nuevo suele ser más
grande que el original. El rebuilder escribía el nuevo tamaño en la fila del
propio ID, así que el juego seguía leyendo el tamaño viejo (más pequeño),
cargaba el stream truncado y la descompresión se rompía a mitad (la corrupción
empezaba ~byte 21000 para ID 7461). Además, escribir en la fila propia
machacaba el tamaño del archivo *anterior*.

**Fix aplicado (2026-06-29):**
- Nuevo módulo `tools/datafat.py` centraliza el parseo correcto de la FAT
  (`size` real = `size_field` de la fila siguiente).
- `tools/patch_dec.py` ahora escribe el nuevo tamaño en el `size_field` de la
  fila **i+1** (sin tocar la fila propia → no corrompe al vecino).
- `extract_all.py`, `extract_dialogue.py` y `parse_archive.py` leen el tamaño
  real (fila siguiente); antes ~500 `.dec` salían truncados.
- Parcheo directo (`patch_compressed.py`, `apply_translation.py`): el byte
  absoluto del stream es `offset + 12 + comp_pos` (antes `+16`, residuo del
  header viejo).
- `lz77.decompress()` ahora es estricto: recorta exactamente a `comp_size` y
  falla si el stream está truncado, en lugar de devolver salida parcial.

---

## Limitaciones actuales

1. **Scripts no soportados por el rebuilder:** Solo los 48 scripts tipo `SCRIPT_DIALOGUE` pueden usar `--rebuild`. Los `TEXT_HEAVY`, `DATA_OR_TABLE` y `TIM2_TEXTURE` requieren edición manual del `.dec`.
2. **Traducciones que no caben en padding:** Si una traducción excede el padding local (~150-190 bytes), el rebuilder la marca `needs_shift` y no la aplica. Para esas se necesita un modo `shift` (Fase 5 del plan, no implementado).
3. **Extracción con heurística:** El extractor usa reglas de idioma japonés para filtrar opcodes, pero no es un parseador de bytecode perfecto. Puede haber falsos positivos/negativos.
4. **Fuente:** Requiere texturas de reemplazo en PCSX2 para caracteres españoles (áéíóúñ¡¿). Ver `Replacement/`.
5. **Métricas de glifo:** El espaciado de caracteres puede verse raro (el ancho del glifo cirílico original no coincide con el español).

---

# Paso para que funcione el parche

En la carpeta `Replacement/` se encuentra el PNG que deberás mover a la carpeta de texturas de PCSX2 para que los caracteres españoles se rendericen correctamente. Si tienes dudas contacta en: https://www.facebook.com/share/p/1HRfueK2eB/
