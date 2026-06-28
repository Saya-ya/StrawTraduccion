# Traducción de Strawberry Panic! (PS2) — Guía del proceso

## ⚠️ Aviso Importante y Legal

* **Traducción 100% Gratuita:** Este parche es un proyecto hecho de fans para fans y su distribución es **completamente gratuita**. Queda estrictamente prohibida su venta o comercialización. Si pagaste por esta traducción o la descargaste de un sitio de pago, te han estafado.
* **Requiere ISO Original:** Para utilizar estas herramientas y aplicar el parche, es requisito indispensable que utilices una copia de seguridad (ISO) extraída de **tu propio juego original**. Este proyecto **NO** incluye, distribuye ni enlaza a ROMs o ISOs con derechos de autor . Por favor, apoya a los desarrolladores originales, en caso de que una version traducida orignal por parte de la compañia es licenciada en tu pais favor de borrar esta version y adquirir la orignal.

---

## Arquitectura del juego

El juego guarda los textos en **dos lugares distintos**:


## Arquitectura del juego

El juego guarda los textos en **dos lugares distintos**:

| Archivo | Qué contiene | Encoding |
|---------|-------------|----------|
| `SLPS_256.11` (ELF) | Menús, sistema, descripciones de personajes | **Shift-JIS** |
| `Data.bin` (archivos LZ77) | Diálogos del juego (escenas) | **UTF-16LE** |

`Data.bin` es un archive con **27,411 archivos** internos. De esos, **997** están comprimidos con LZSS y contienen los scripts. El resto son audio (SS2 ADPCM) y texturas (TIM2).

---

## Cómo funciona la extracción

### 1. Textos del ELF (menús, descripciones)

El ELF contiene los textos del sistema en **Shift-JIS**. La extracción escanea el archivo binario buscando secuencias de bytes que formen caracteres japoneses válidos (rango 0x81-0x9F + 0xE0-0xEF como lead bytes).

```
extract_dialogue.py --elf
```

Encuentra strings como:
- `メモリーカード` (Memory Card)
- `聖ミアトル女学園４年月組在籍。` (descripción de personaje)
- `セーブデータを読み込み中です。` (cargando datos...)

El offset en el CSV es la posición exacta dentro del ELF donde empieza el texto.

### 2. Textos de los scripts (diálogos)

Cada script está comprimido con **LZSS** (variante de PS2). El proceso:

1. Se descomprime el archivo con `lz77.py`
2. Se escanea el output buscando secuencias **UTF-16LE** que formen texto japonés
3. Se filtran strings que tengan hiragana/katakana (para descartar opcodes)
4. El resultado va al CSV con `[file_id, offset, texto_original]`

```
extract_dialogue.py --csv dialogo.csv
```

---

## Cómo funciona la traducción (apply_translation.py)

### El problema del espacio

El japonés es muy compacto: 1 kanji = 1 concepto. El español necesita varias letras.

```
園の奧深くに     = 6 caracteres = 12 bytes (UTF-16LE)
En el jardín     = 12 caracteres = 24 bytes (UTF-16LE) ← NO CABE
Jardín           = 6 caracteres = 12 bytes ← SÍ CABE
```

La herramienta **compara el tamaño en bytes** del original vs la traducción:
- Si cabe → parchea
- Si no cabe → salta con warning

### El problema del compresor (y la solución)

Los archivos LZ77 no se pueden recomprimir (el compresor tiene un bug que causa pantalla negra). En vez de recomprimir, **modificamos bytes directamente en el stream comprimido original**.

El truco: en LZSS, cada byte del output puede ser de dos tipos:
- **LITERAL**: el byte está copiado tal cual en el stream comprimido → se puede modificar
- **MATCH**: el byte es una referencia a datos anteriores → NO se puede modificar sin romper todo

La mayoría de los bytes de texto (~85%) son LITERAL. `patch_compressed.py` traza la descompresión para encontrar qué bytes del stream comprimido corresponden a cada byte del texto, y solo modifica los LITERAL.

```
Texto descomprimido    Stream comprimido (bytes LITERAL)
────────────────────   ─────────────────────────────────
"園の奧深くに"          pos 1001 → 0x57 (園 byte 1)
                        pos 1002 → 0x12 (園 byte 2)
                        pos 1003 → 0x30 (の byte 1)
                        ... etc
```

### El mapeo de fuente (español → cirílico)

El juego no tiene `á`, `é`, `ñ` en su fuente original. Para resolverlo, se reusaron glifos de **caracteres cirílicos** que sí existen en la fuente del juego, reemplazando sus texturas vía PCSX2.

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

### Shift-JIS: el peligro del off-by-one

En Shift-JIS, los caracteres cirílicos NO son consecutivos. Hay un `Ё` (U+0401) metido entre `Е` y `Ж`:

```
Posición real en Shift-JIS:
  0x8443 = Г    0x8444 = Д    0x8445 = Е    0x8446 = Ё ← este desplaza todo
  0x8447 = Ж    0x8448 = З    0x8449 = И    0x844A = Й    ...
```

Por eso `apply_translation.py` **no hardcodea bytes Shift-JIS**. Convierte español → cirílico (Unicode) y luego deja que Python haga `.encode('shift-jis')` con la tabla correcta.

---

## Flujo de trabajo completo

```
┌─────────────────────────────────────────────────────────┐
│ 1. EXTRACCIÓN                                           │
│                                                         │
│   python extract_dialogue.py --csv dialogo.csv          │
│   python extract_dialogue.py --elf --csv sistema.csv    │
│                                                         │
│   → CSV con columnas:                                   │
│     [source, file_id, offset, original_text, translated]│
├─────────────────────────────────────────────────────────┤
│ 2. TRADUCCIÓN (MANUAL)                                  │
│                                                         │
│   El traductor edita el CSV, llenando la columna        │
│   "translated_text" con español REAL (á, é, í, ó...)   │
├─────────────────────────────────────────────────────────┤
│ 3. APLICACIÓN                                           │
│                                                         │
│   python apply_translation.py dialogo.csv               │
│                                                         │
│   → Sustituye español→cirílico (en código, no en CSV)   │
│   → Parchea Data.bin y SLPS_256.11                      │
├─────────────────────────────────────────────────────────┤
│ 4. CONSTRUCCIÓN DE ISO                                  │
│                                                         │
│   python traduccion_tools/build_iso.py                  │
│   python traduccion_tools/inject_elf.py                 │
│                                                         │
│   → Reemplaza Data.bin y ELF en la ISO original         │
├─────────────────────────────────────────────────────────┤
│ 5. PRUEBA EN PCSX2                                      │
│                                                         │
│   Cargar work/Strawberry_translated.iso                 │
│   Verificar que el texto traducido se ve correctamente  │
└─────────────────────────────────────────────────────────┘
```

---

## Estructura del proyecto

```
traduccion/
├── originales/           # Archivos originales (recuerda extraer el iso legal)
│   ├── Data.bin
│   ├── SLPS_256.11
│   └── Strawberry.iso
├── work/                    # Archivos de trabajo (se regeneran)
│   ├── Data_patched.bin
│   ├── SLPS_256.11_translated
│   └── Strawberry_translated.iso
├── tools/                   # Herramientas de bajo nivel
│   ├── lz77.py              # Decompresor LZSS de PS2
│   ├── parse_archive.py     # Analiza la FAT de Data.bin
│   ├── extract_all.py       # Extrae archivos individuales
│   └── patch_compressed.py  # Parchea bytes en stream LZSS
├── traduccion_tools/        # Herramientas de traducción
│   ├── extract_dialogue.py  # Extrae textos → CSV
│   ├── apply_translation.py # Aplica CSV → parchea archivos
│   ├── build_iso.py         # Reconstruye la ISO
│   └── inject_elf.py        # Inyecta el ELF traducido en la ISO
├── textos/                  # CSVs de traducción
└── LEEME.md                 # Este documento
```

---

# Paso para que funcione el parche

En la carpeta replacement se encuentra el png que deberas mover a la direccion de tu carpeta de pcsx2 para que todo fucione bien, si tienes dudas contactame en Facebook en mi pagina https://www.facebook.com/share/p/1HRfueK2eB/ 

---

## Limitaciones actuales

1. **Espacio de texto fijo**: la traducción debe caber en los mismos bytes que el original. Si no cabe, se salta.
2. **Bytes MATCH**: si un byte del texto es MATCH en vez de LITERAL, no se puede parchear (~15% de los casos).
3. **Fuente**: requiere texturas de reemplazo en PCSX2 para los caracteres españoles (áéíóúñ).
4. **Métricas de glifo**: el espaciado de los caracteres nuevos puede verse raro (el ancho original del carácter cirílico no coincide con el español).

## 