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

## Método 2: Recompresión (`patch_dec.py`)

**Usar cuando:** el parcheo directo falla (MATCH, o la traducción no cabe). **Ahora funcional gracias al fix del header LZ77 (ver sección técnica abajo).**

Flujo:
1. Descomprime el `.dec` → obtiene el script sin comprimir
2. Modificas el texto **directamente en el `.dec`** (con editor hex o script)
3. Recomprime con el compresor corregido
4. Inyecta en `Data.bin`

```bash
# Paso 1: Extraer scripts (una vez)
python tools/extract_all.py --type lz77
# → work/scripts_extraidos/ID_07461.dec

# Paso 2: Modificar el .dec (reemplazar texto UTF-16LE)
# (manual o con script)

# Paso 3: Recomprimir e inyectar
cp originales/Data.bin work/Data_patched.bin
python tools/patch_dec.py --id 7461 --dec work/scripts_extraidos/ID_07461.dec
```

**Ventaja sobre parcheo directo:**
- Sin límite de MATCH (todo el texto se puede cambiar)
- Sin límite estricto de tamaño (el slot tiene ~32KB, usamos ~4KB)

**Limitación real:** el `.dec` es bytecode con punteros internos. Si estiras un texto, los datos siguientes se desplazan y los punteros se rompen. Para traducciones largas se necesita un *script rebuilder* que actualice los punteros.

---

## Flujo de trabajo completo

```
┌──────────────────────────────────────────────────────────────┐
│ 1. EXTRACCIÓN                                                │
│                                                              │
│   # Scripts comprimidos (Data.bin)                           │
│   python tools/extract_all.py --type lz77                    │
│   → work/scripts_extraidos/ID_*.dec  (997 archivos)          │
│                                                              │
│   # Textos a CSV                                             │
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
├──────────────────────────────────────────────────────────────┤
│ 3. APLICACIÓN                                                │
│                                                              │
│   # Opción A: Parcheo directo (rápido, limitado)             │
│   python traduccion_tools/apply_translation.py dialog.csv    │
│                                                              │
│   # Opción B: Recompresión (para lo que falle en A)          │
│   # 1. Modificar el .dec a mano                              │
│   # 2. python tools/patch_dec.py --id <ID> --dec <archivo>   │
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
│   ├── lz77.py                # Decompresor/compresor LZSS de PS2
│   ├── patch_compressed.py    # Parcheo directo en stream LZSS
│   ├── patch_dec.py           # Script Rebuilder: recomprime + inyecta
│   ├── extract_all.py         # Extrae archivos individuales de Data.bin
│   └── parse_archive.py       # Analiza la FAT de Data.bin
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

1. **Bytecode con punteros:** Los `.dec` son scripts con bytecode que referencia posiciones absolutas. Estirar un texto desplaza los datos siguientes y rompe los punteros. Se requiere un *script rebuilder* que actualice referencias internas para traducciones de longitud libre.
2. **Extracción con heurística:** El extractor usa reglas de idioma japonés para filtrar opcodes, pero no es un parseador de bytecode perfecto. Puede haber falsos positivos.
3. **Bytes MATCH (solo parcheo directo):** ~15% de los bytes de texto son referencias y no se pueden modificar sin recompresión.
4. **Fuente:** Requiere texturas de reemplazo en PCSX2 para caracteres españoles (áéíóúñ¡¿).
5. **Métricas de glifo:** El espaciado de caracteres puede verse raro (el ancho del glifo cirílico original no coincide con el español).

---

# Paso para que funcione el parche

En la carpeta `Replacement/` se encuentra el PNG que deberás mover a la carpeta de texturas de PCSX2 para que los caracteres españoles se rendericen correctamente. Si tienes dudas contacta en: https://www.facebook.com/share/p/1HRfueK2eB/
