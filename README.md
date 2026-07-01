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

La forma más fácil y recomendada para traducir el juego es utilizando la **Aplicación Web** (Translation Manager) incluida en el proyecto. Esta interfaz gráfica te permite buscar textos, traducirlos, verificar si caben en el espacio (fit-check) y generar la ISO traducida, todo desde el navegador.

### Requisitos

- Python 3 instalado en tu PC
- Tu ISO original del juego (extraída de tu propio disco)
- La carpeta `StrawTraduccion` (este proyecto)
- PCSX2 para probar

### Paso 1 — Preparar los archivos (una sola vez)

Abre la ISO con WinRAR o 7-Zip y extrae estos archivos a la carpeta `originales/`:
- `Data.bin`
- `SLPS_256.11`

Después ejecuta en la terminal:

```bash
python tools/extract_all.py --type lz77
python traduccion_tools/extract_dialogue.py
python traduccion_tools/extract_dialogue.py --elf
```

Esto extrae los textos del juego y genera el archivo base `textos/dialogo.csv` con ~55,000 líneas de diálogo.

### Paso 2 — Importar y Traducir en la Web App

1. Inicia el servidor web ejecutando en tu terminal:
   ```bash
   python run_webapp.py
   ```
2. Abre tu navegador y ve a `http://127.0.0.1:8080`.
3. Ve a la pestaña **Importar** y haz clic en el botón para cargar los textos extraídos en la base de datos de la aplicación web.
4. Usa las pestañas **Scripts** y **Buscar** para traducir:
   - Puedes buscar textos específicos en japonés o español.
   - Haz clic en cualquier texto para abrir el editor en línea (se guarda automáticamente con `Ctrl+Enter`).
   - El sistema te mostrará instantáneamente un indicador de ajuste (`🟢 Cabe`, `🟡 Ajustado`, `🔴 No cabe / needs_shift`) para que sepas si tu traducción entra en la memoria del juego. Escribe normalmente con tildes y eñes.

**Importante**: solo los textos cuyos scripts estén en la lista de soportados (Soportado ✓) y tengan espacio suficiente podrán ser aplicados sin romper el juego.

### Paso 3 — Aplicar las traducciones y generar la ISO

Desde la misma aplicación web, ve a la pestaña **Build** y haz clic en **Ejecutar Build Completo**. El sistema automáticamente:
- Exportará tus traducciones de la base de datos.
- Reconstruirá los archivos comprimidos.
- Inyectará todo en `Data.bin` y en el `ELF`.
- Generará la ISO final.

La ISO traducida quedará lista en `work/Strawberry_translated.iso`.

### Paso 4 — Probar en PCSX2

Copia el archivo PNG de la carpeta `Replacement/` a la carpeta de texturas de PCSX2 (`textures/SLPS-25611/`). Esto es necesario para que los caracteres españoles (á, é, í, ó, ú, ñ, ¡, ¿) se vean correctamente.

### Colaboración y Delegación

Si trabajas en equipo, puedes usar la pestaña **Delegar** para descargar fragmentos del CSV, traducirlos en Excel/LibreOffice y volver a importarlos a la base de datos.

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

**Usar cuando:** el script NO es de tipo SCRIPT_DIALOGUE, o necesitas forzar
compresión sin matches (`--all-literal`).

Flujo:
1. Modifica el texto **directamente en el `.dec`** (con editor hex o script)
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

```text
┌──────────────────────────────────────────────────────────────┐
│ 1. EXTRACCIÓN (una sola vez por terminal)                    │
│                                                              │
│   python tools/extract_all.py --type lz77                    │
│   python traduccion_tools/extract_dialogue.py                │
│   python traduccion_tools/extract_dialogue.py --elf          │
├──────────────────────────────────────────────────────────────┤
│ 2. IMPORTACIÓN Y TRADUCCIÓN (VÍA WEB APP)                    │
│                                                              │
│   python run_webapp.py                                       │
│                                                              │
│   → Navegar a http://127.0.0.1:8080                          │
│   → Ir a "Importar" para cargar el CSV en la Base de Datos.  │
│   → Usar "Scripts" o "Buscar" para editar y traducir.        │
│   → El guardado es automático y avisa si el texto cabe (🟢)  │
├──────────────────────────────────────────────────────────────┤
│ 3. CONSTRUCCIÓN DE ISO (VÍA WEB APP)                         │
│                                                              │
│   → En la app web, ir a la pestaña "Build".                  │
│   → Click en "Ejecutar Build Completo".                      │
│                                                              │
│   El proceso hará automáticamente:                           │
│   - cp originales/Data.bin work/Data_patched.bin             │
│   - tools/patch_dec.py --rebuild para los scripts traducidos │
│   - traduccion_tools/build_iso.py                            │
│   - traduccion_tools/inject_elf.py                           │
│                                                              │
│   → Resultado: work/Strawberry_translated.iso                │
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
│   ├── extract_dialogue.py    # Extrae textos JP → CSV (heurística 8 rangos Unicode)
│   ├── apply_translation.py   # Aplica CSV: parcheo directo (ambos tipos)
│   ├── build_iso.py           # Reconstruye ISO con Data.bin modificado
│   ├── inject_elf.py          # Inyecta ELF traducido en la ISO
│   └── searcher.py            # Busca frases en los CSVs
├── pine_*.py                  # Herramientas PINE (RAM patching en vivo)
├── textos/                    # CSVs de traducción
│   └── dialogo.csv            # ~55,000 textos (SCRIPTS + ELF)
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
6. **Bios y descripciones en ELF:** Las descripciones de personajes y modos de juego en el ELF tienen espacio limitado (~112-128 bytes). Las traducciones deben ser concisas para caber.


---

# Paso para que funcione el parche

En la carpeta `Replacement/` se encuentra el PNG que deberás mover a la carpeta de texturas de PCSX2 para que los caracteres españoles se rendericen correctamente. Si tienes dudas contacta en: https://www.facebook.com/share/p/1HRfueK2eB/
