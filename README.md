# StrawTraduccion

Web local en Rust para traducir y reconstruir *Strawberry Panic!* de PS2.

El flujo principal está migrado a Rust: importación, edición, búsqueda, SQLite, rebuild de scripts, ELF, texturas TIM2, generación de ISO y logs de build desde la web.

## Aviso Legal

- Proyecto fan gratuito. No vender ni comercializar.
- Requiere archivos extraídos de tu propio disco original.
- El repositorio no incluye ni enlaza ROMs, ISOs ni datos protegidos.

## Requisitos

- Rust stable toolchain.
- Archivos originales colocados en `originales/`:
  - `originales/Data.bin`
  - `originales/SLPS_256.11`
  - `originales/Strawberry_patched.iso`

## Ejecutar

```bash
./run_rust.sh
```

O directamente:

```bash
cargo run --bin strawtraduccion
```

Abrir:

```text
http://127.0.0.1:8080
```

## Flujo Recomendado

1. Ir a `/import`.
2. Extraer scripts LZ77.
3. Importar textos a SQLite.
4. Traducir desde `/scripts` o buscar desde `/search`.
5. Revisar texturas desde `/textures` si hace falta.
6. Ir a `/build`.
7. Ejecutar `Build completo`.
8. Probar `work/Strawberry_translated.iso`.

El panel de build muestra logs en vivo desde `work/build_temp/build.log`.

## Estructura

```text
StrawTraduccion/
├── crates/
│   ├── straw-core      # formatos binarios, FAT, LZ77, scripts, ELF, TIM2, ISO
│   ├── straw-db        # SQLite, FTS, settings, import/export
│   └── straw-web       # Axum + Askama web local
├── originales/         # archivos originales locales, no distribuidos
├── work/               # archivos de trabajo regenerables
├── texturas/           # PNGs editados + manifest local
├── work_texturas/      # inventario TIM2 y streams parcheados
├── Replacement/        # assets/reemplazos locales si se usan
├── docs/               # documentación
├── run_rust.sh         # launcher
├── Cargo.toml
└── Cargo.lock
```

## Funcionalidad Rust

- Extracción LZ77 desde `Data.bin`.
- Análisis/importación de diálogos `.dec`.
- Extracción/importación ELF Shift-JIS.
- Editor web con contador de bytes y límite por segmento.
- Fit checker y marcado `needs_shift`.
- Export CSV temporal de build.
- Rebuild local-slack de scripts.
- Recompresión LZ77.
- Parcheo ELF.
- Inventario TIM2 directo/anidado.
- Export PNG TIM2.
- Parcheo TIM2 `preserve_palette`.
- Inyección de texturas en `Data_patched.bin`.
- Generación de ISO.
- Inyección ELF en ISO.
- Historial y logs de build.

## Notas Técnicas

- Textos de scripts: UTF-16LE con terminador.
- Textos ELF: Shift-JIS.
- El rebuild actual usa modo conservador `local-slack`: no mueve punteros.
- Si una traducción no cabe, se marca `needs_shift`.
- En FAT, el `size_field` de una fila describe el archivo anterior; la lógica correcta está en `straw-core::datafat`.

## Verificación

```bash
cargo fmt --all
cargo test --workspace
cargo check --workspace
```
