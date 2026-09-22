# StrawTraduccion

Aplicación web local en Rust para traducir, revisar recursos y reconstruir *Strawberry Panic! Girls' School in Fullbloom* para PlayStation 2.

StrawTraduccion reúne en un solo flujo la extracción de textos, la edición asistida por capacidad, el tratamiento de imágenes TIM2, el parcheo de `Data.bin` y del ELF, y la generación de una ISO de prueba. El repositorio contiene la herramienta y su documentación; los archivos originales del juego deben ser aportados por cada usuario.

[innovStudio](https://innovstudio.net/) | [YouTube](https://www.youtube.com/@InnovatedSk) | [Proyecto Strawberry Panic](https://innovstudio.net/proyectos/strawberry-panic)

![innovStudio](https://innovstudio.net/images/banner.png)

## Alcance

La aplicación está orientada al proyecto de traducción al español, aunque el pipeline admite un mapa de glifos personalizado. Sus funciones principales son:

- Extracción de scripts LZ77 desde `Data.bin`.
- Importación de diálogos y textos ELF a SQLite.
- Edición web con paginación, filtros de estado y búsqueda FTS5.
- Cálculo de capacidad y clasificación `ok`, `tight` o `needs_shift`.
- Reconstrucción conservadora de scripts UTF-16LE.
- Parcheo de textos Shift-JIS dentro de `SLPS_256.11`.
- Inventario de recursos TIM2 directos, comprimidos y anidados.
- Catálogo de texturas, agrupación de duplicados por contenido y carga de PNG editados.
- Parcheo automático de métricas horizontales para glifos reemplazados.
- Generación de `Data_patched.bin` y de una ISO final verificable.
- Logs operativos e historial de builds.

La herramienta no es un editor genérico de juegos de PS2. Varias decisiones dependen de la estructura observada en esta edición de *Strawberry Panic!*.

## Aviso legal

- Proyecto fan, gratuito y sin fines de lucro.
- Requiere archivos obtenidos de una copia legítima del juego.
- No se incluyen ni enlazan ISOs, ROMs, `Data.bin`, ELF u otros datos completos protegidos.
- No se debe vender ni redistribuir el juego modificado como producto comercial.
- Los derechos de *Strawberry Panic!* y sus recursos pertenecen a sus respectivos propietarios.
- El código Rust del workspace declara licencia MIT en `Cargo.toml`.

## Requisitos

- Rust estable y Cargo.
- Navegador moderno.
- Conexión a Internet para Tailwind CSS y HTMX, cargados actualmente desde CDN.
- Varios gigabytes libres para copias de `Data.bin`, archivos temporales y la ISO resultante.
- Memoria suficiente para procesar archivos grandes; algunas etapas cargan recursos completos en memoria.

La aplicación debe iniciarse desde la raíz del repositorio. Las rutas de trabajo, el host y el puerto son actualmente fijos.

## Archivos originales

Coloca estos archivos con nombres exactos:

```text
originales/
├── Data.bin
├── SLPS_256.11
└── Strawberry_patched.iso
```

`Strawberry_patched.iso` es la imagen base en la que se inyectan `Data_patched.bin` y el ELF traducido. El pipeline localiza ambos contenidos mediante firmas y rechaza cualquier inyección que extienda la ISO.

No se utiliza `SYSTEM.CNF` en el pipeline Rust actual.

## Inicio rápido

Desde la raíz del repositorio:

```bash
./run_rust.sh
```

Comando equivalente:

```bash
cargo run --bin strawtraduccion
```

La interfaz queda disponible en:

```text
http://127.0.0.1:8080
```

Comprobación básica del servidor:

```bash
curl http://127.0.0.1:8080/health
```

Para habilitar logs más detallados:

```bash
RUST_LOG=straw_web=debug,tower_http=debug cargo run --bin strawtraduccion
```

Compilación release:

```bash
cargo build --release --bin strawtraduccion
./target/release/strawtraduccion
```

El binario release también debe ejecutarse desde la raíz, porque las rutas son relativas al directorio de trabajo.

## Flujo de trabajo

### Primera importación

1. Abre `/import`.
2. Extrae los scripts LZ77 desde `originales/Data.bin`.
3. Importa los textos de scripts y ELF a SQLite.
4. Revisa en `/scripts` la clasificación asignada a cada script.
5. Configura el idioma objetivo y el mapa de glifos antes de preparar un build.

La base persistente se guarda en:

```text
work/translation_manager.db
```

Una reimportación conserva traducciones cuando coinciden origen, ID de script, offset y texto original. La pantalla de importación también permite fusionar traducciones desde otra base compatible; las filas que no coinciden se omiten.

### Traducción

1. Usa `/scripts` para navegar por archivo o `/search` para una búsqueda global.
2. Filtra por `untranslated`, `translated`, `warning` o `needs_shift`.
3. Guarda cada traducción y revisa el estado de capacidad calculado.
4. Resuelve primero los textos que excedan el método disponible para su script.
5. Comprueba el resultado dentro del juego; la capacidad binaria no sustituye la revisión visual y lingüística.

Atajos del editor:

| Atajo | Acción |
|---|---|
| `Ctrl+Enter` / `Cmd+Enter` | Guardar traducción. |
| `Esc` | Cancelar la edición. |
| `/` | Enfocar la búsqueda dentro del script. |

La búsqueda utiliza SQLite FTS5 sobre texto original y traducido. No es una búsqueda arbitraria por substring ni indexa los offsets.

### Texturas

1. Abre `/textures` y genera un inventario actualizado.
2. Examina `/textures/catalog` para localizar interfaces, fuentes y otras imágenes.
3. Usa `/textures/duplicates` para identificar recursos idénticos.
4. Edita un solo representante de cada grupo de duplicados.
5. Conserva exactamente las dimensiones del PNG exportado.
6. Sube el PNG desde `/textures/upload` o desde el acceso directo del grupo.
7. Revisa `texturas/manifest.json` y ejecuta un build con imágenes.

No es necesario editar todas las copias de una textura duplicada. Al subir una por nombre exportado o por hash, la aplicación expande el cambio a todos los registros que compartían el contenido original y crea una entrada de manifest para cada destino.

### Build

1. Abre `/build`.
2. Elige `Completo`, `Solo textos` o `Solo texturas`.
3. Inicia el build y sigue el progreso en la pestaña Logs.
4. Verifica que no existan scripts, textos ELF o streams omitidos.
5. Prueba `work/Strawberry_translated.iso` en un entorno controlado.

El build completo vuelve a extraer scripts desde el `Data.bin` original y genera streams temporales nuevos. Esto evita que intermediarios antiguos contaminen la salida.

## Interfaz web

| Ruta | Función |
|---|---|
| `/` | Resumen del proyecto y accesos principales. |
| `/health` | Estado mínimo del servidor. |
| `/import` | Extracción, importación y fusión de bases de traducción. |
| `/scripts` | Lista de scripts, progreso y modo de reconstrucción. |
| `/scripts/:id` | Editor paginado de un script. |
| `/search` | Búsqueda FTS5 y filtros globales. |
| `/textures` | Estado, tareas y log del pipeline TIM2. |
| `/textures/catalog` | Catálogo completo de imágenes exportadas. |
| `/textures/duplicates` | Grupos de contenido idéntico. |
| `/textures/upload` | Carga de PNG y actualización del manifest. |
| `/build` | Build integral, operaciones manuales, logs e historial. |
| `/settings` | Preferencias persistentes del proyecto. |

La navegación principal es compartida por todas las vistas. Las páginas de detalle añaden una navegación contextual sin sustituir la navegación global.

## Modelo de traducción

Los textos editables proceden de dos fuentes:

| Fuente | Contenido principal | Codificación al reconstruir |
|---|---|---|
| `Data.bin` | Diálogos y registros de scripts LZ77 | UTF-16LE |
| `SLPS_256.11` | Menús, sistema y descripciones | Shift-JIS |

SQLite almacena el texto original, traducción, offset, capacidad, estado de ajuste y modo de reconstrucción. El CSV de build sólo contiene filas traducidas y se genera automáticamente antes de un build completo.

### Estados de capacidad

- `ok`: quedan al menos 20 bytes libres.
- `tight`: la traducción cabe, pero quedan menos de 20 bytes.
- `needs_shift`: excede la capacidad local registrada.
- `unchecked`: no hay traducción evaluable.

Para scripts se cuentan los bytes UTF-16LE y el terminador de dos bytes. Para ELF se usa Shift-JIS sin añadir un terminador nuevo.

El contador del navegador es orientativo. La validación autoritativa ocurre en Rust durante el guardado y, finalmente, durante el parcheo. Con mapas personalizados conviene revisar siempre el reporte del build.

## Reconstrucción de scripts

Los scripts se clasifican antes de ser parcheados:

| Clasificación | Método actual |
|---|---|
| `shift_suffix_safe` | Permite ampliar un registro inline reconocido y desplazar su sufijo. |
| `local_slack_only` | Reemplaza dentro del segmento y del relleno local disponible. |
| `pointer_table_needed` | Usa el fallback conservador; no reubica tablas de punteros. |
| `unknown_unsafe` | Usa el fallback conservador y requiere revisión adicional. |

### `local-slack`

- Sustituye texto dentro del segmento UTF-16 detectado.
- Puede consumir padding cero contiguo.
- Actualiza el prefijo de longitud reconocido.
- Conserva datos no textuales posteriores.
- Omite la traducción si no cabe de forma segura.

### `shift-suffix`

- Se limita a registros inline cuya estructura fue reconocida como segura.
- Inserta bytes y desplaza los comandos posteriores del mismo registro.
- Actualiza la longitud almacenada.
- Conserva y verifica el sufijo binario.
- Sigue sujeto a que el script recomprimido quepa en su slot de `Data.bin`.

Cada script reconstruido se comprime de nuevo, se descomprime para verificar el round-trip y se inyecta sólo si respeta la capacidad del slot. El sistema no implementa todavía reubicación global de tablas de punteros.

## Parcheo ELF

El ELF se reconstruye siempre desde `originales/SLPS_256.11`:

- Sólo se procesan filas traducidas con origen `ELF`.
- La traducción se codifica como Shift-JIS después de aplicar el mapa de glifos.
- Un texto debe caber en el campo original efectivo.
- Las traducciones cortas se rellenan con espacios.
- Offsets inválidos o textos demasiado largos se registran y omiten sin corromper el resto del ELF.

Por esta razón, el resumen final de `Entradas ELF parcheadas`, `demasiado largas` y `omitidas` debe revisarse en cada build.

## Glifos y fuentes

El mapa de glifos relaciona el carácter deseado con un slot ya existente en la fuente del juego:

```text
carácter deseado -> carácter donante
```

El mapa español reserva slots cirílicos y el `＠` de ancho completo para caracteres ausentes en la fuente original. Entre otros, contempla vocales acentuadas, `ñ`, `Ñ`, signos de apertura y variantes de corazón.

El idioma `Personalizado` acepta un objeto JSON como:

```json
{
  "ã": "Г",
  "ç": "Д"
}
```

Los donantes personalizados deben pertenecer al conjunto reservado y ser codificables en Shift-JIS. Los alias que usan el mismo donante comparten dibujo y métricas.

### Recurso de fuente fuera de FAT

`Data.bin` contiene un recurso LZ77 señalado por el puntero de cabecera en `0x0C`. Está antes del primer archivo de la FAT, por lo que el inventario le asigna el ID sintético `4294967295` (`u32::MAX`). Este ID no representa una fila real.

En la imagen original conocida del proyecto, este recurso comienza en `0x60000`, contiene 59 hojas TIM2 y expone la hoja cirílica como:

```text
ID_4294967295_T001_P00_256x256.png
```

El código no fija esas cantidades: sigue el puntero y valida los límites en cada inventario y build.

### Métricas automáticas

Cuando cambia el alfa visible de un slot donante:

- Se calculan de nuevo su posición X y ancho visible.
- Se intenta conservar un píxel de margen horizontal.
- El nuevo rectángulo nunca sale del slot original ni invade el glifo vecino.
- Se mantienen Y, altura, página y estructura de la tabla.
- Los alias del mismo donante se deduplican.

No se añaden caracteres a la tabla de fuente; se reutilizan entradas existentes de forma controlada.

## Inventario y parcheo TIM2

El inventario analiza:

- Recursos TIM2 directos de la FAT.
- Recursos LZ77 completos.
- Streams LZ77 anidados dentro de contenedores.
- El recurso de fuente previo a la FAT.
- Múltiples TIM2 y múltiples pictures dentro de un mismo recurso.

Archivos generados:

```text
work_texturas/output/all_textures/
├── textures.json
├── textures.csv
└── png/
```

Ejemplo de nombre directo:

```text
ID_15065_T003_P00_176x288.png
```

Ejemplo de nombre anidado:

```text
ID_15111_L000050_T003_P00_176x288.png
```

`L000050` identifica el offset hexadecimal del stream LZ77 anidado dentro del contenedor.

### Duplicados

Cada textura recibe un hash FNV-1a de 64 bits calculado sobre:

- Tipo de imagen.
- Ancho y alto.
- Cantidad de colores de la CLUT.
- Datos indexados de imagen.
- Datos originales de la CLUT.

El hash se usa como identificador determinista de contenido, no como garantía criptográfica. Dos archivos sólo se agrupan si comparten el mismo contenido relevante; tener las mismas dimensiones no es suficiente.

La estrategia recomendada es conservar una copia de trabajo por grupo, editarla y subirla. La aplicación copia el PNG a cada destino y crea una entrada específica en `texturas/manifest.json` para cada ID, TIM2, picture y offset anidado afectados.

### Restricciones de edición

- El PNG debe conservar exactamente el ancho y alto originales.
- El modo soportado es `preserve_palette`.
- El parcheo está dirigido a TIM2 indexados de tipos 3, 4 y 5.
- Los píxeles se asignan al color más cercano de la paleta existente.
- La paleta original no se reemplaza.
- El stream recomprimido debe caber en su slot original.

Estas restricciones priorizan builds reproducibles y evitan alterar estructuras TIM2 que el juego espera encontrar intactas.

## Modos de build

| Modo | Scripts | ELF | Texturas | ISO |
|---|---:|---:|---:|---:|
| `Completo` | Sí | Traducido | Si existe manifest | Sí |
| `Solo textos` | Sí | Traducido | No | Sí |
| `Solo texturas` | No | Original | Obligatorio | Sí |

Todos los modos requieren los tres archivos de `originales/`. `Solo texturas` también requiere `texturas/manifest.json`. En modo completo, la etapa TIM2 se omite si no existe un manifest.

El selector de procesos queda limitado entre 1 y 8 según el equipo. Actualmente se conserva como preferencia y contexto de ejecución; las escrituras críticas sobre `Data.bin` siguen siendo secuenciales para evitar corrupción.

### Build completo

El pipeline ejecuta, según el modo:

1. Validación de originales y prerequisitos.
2. Exportación de traducciones desde SQLite.
3. Copia limpia de `Data.bin`.
4. Extracción fresca y parcheo de scripts.
5. Parcheo del ELF.
6. Generación e inyección de streams TIM2 nuevos.
7. Copia de la ISO base e inyección de `Data_patched.bin`.
8. Inyección del ELF traducido u original.

### Operaciones manuales

La pestaña Manual permite aislar exportación CSV, preparación de Data, scripts, ELF e ISO. Es útil para diagnóstico, pero opera sobre intermediarios persistentes. Para una salida final reproducible se recomienda el build completo.

## Salidas y datos locales

### Estado persistente

```text
work/translation_manager.db
```

Contiene scripts, textos, preferencias, historial de builds e índice FTS5.

### Importación

```text
work/scripts_extraidos/ID_<id>.dec
work/import_temp/import.log
work/import_temp/imported_translation_manager.db
```

### Build

```text
work/build_temp/dialogo.csv
work/build_temp/build.log
work/Data_patched.bin
work/SLPS_256.11_translated
work/Strawberry_translated.iso
```

### Texturas

```text
work_texturas/output/all_textures/
work_texturas/patched/
work_texturas/texture.log
texturas/uploads/
texturas/manifest.json
```

`originales/`, `work/`, `work_texturas/`, los uploads y el manifest son datos locales o regenerables. No deben añadirse al repositorio archivos completos del juego ni builds derivados.

## Configuración

Las preferencias se almacenan en SQLite:

| Preferencia | Uso actual |
|---|---|
| Idioma UI | Metadato de interfaz; la traducción completa de plantillas aún no está implementada. |
| Idioma objetivo | Selecciona mapa español, inglés vacío o mapa personalizado durante el build. |
| Build por defecto | Preselecciona el modo de build. |
| Procesos | Preselecciona el valor mostrado y registrado para el build. |
| Filas por página | Controla la paginación de scripts entre 10 y 200. |
| Resultados de búsqueda | Limita la búsqueda global entre 10 y 500 filas. |
| Mapa personalizado | Define carácter deseado y slot donante para builds personalizados. |

## Arquitectura

```text
StrawTraduccion/
├── crates/
│   ├── straw-core/     # formatos binarios, rebuild e inyección
│   ├── straw-db/       # SQLite, FTS5, importación y exportación
│   └── straw-web/      # Axum, Askama y orquestación local
├── originales/         # entradas privadas aportadas por el usuario
├── work/               # base, intermediarios y salida ISO
├── work_texturas/      # inventario y streams TIM2 generados
├── texturas/           # uploads y manifest de parches
├── docs/               # documentación técnica adicional
├── run_rust.sh
├── Cargo.toml
└── Cargo.lock
```

| Capa | Tecnología y responsabilidad |
|---|---|
| Web | Axum, Askama, multipart, tareas en segundo plano y logs. |
| Persistencia | SQLite, sqlx y FTS5. |
| Binarios | Rust para FAT, LZ77, scripts, ELF, TIM2 e ISO. |
| Interfaz | HTML renderizado en servidor, Tailwind CSS y HTMX. |

## Decisiones de diseño

### Seguridad binaria antes que expansión implícita

El pipeline nunca extiende silenciosamente slots de `Data.bin`, campos ELF o la ISO. Cada escritura valida capacidad, offsets y tamaño recomprimido. Cuando una operación no es segura, se omite y se registra.

### Originales inmutables

Los builds parten de `originales/` y escriben en `work/`. Un build completo no reutiliza scripts extraídos ni streams TIM2 temporales de una ejecución anterior.

### Transformaciones verificables

Los scripts y texturas LZ77 se descomprimen después de recomprimirlos para comprobar que el resultado representa los bytes esperados. La inyección de ISO valida firmas y límites antes de escribir.

### Duplicación explícita de parches

El manifest mantiene una entrada por destino real, incluso cuando varias texturas comparten hash. Esto conserva trazabilidad y evita depender de deduplicación durante la inyección.

### Separación por responsabilidades

`straw-core` no depende de la web ni de SQLite; concentra formatos y transformaciones. `straw-db` administra el estado de traducción. `straw-web` coordina ambas capas y expone el flujo local.

### Operación local por defecto

El servidor escucha únicamente en `127.0.0.1:8080`. No incluye autenticación ni está diseñado para exponerse directamente a una red pública.

## Particularidades técnicas

- La FAT de `Data.bin` comienza en una ubicación conocida para esta edición y contiene 27,411 filas.
- El campo de tamaño de una fila describe la entrada anterior; esta regla está centralizada en `straw-core::datafat`.
- El formato LZ77 usa un encabezado de 12 bytes.
- Los scripts emplean UTF-16LE y terminadores de dos bytes.
- El ELF usa Shift-JIS.
- `＠` funciona como slot donante para variantes de corazón.
- Los locks de importación, build y texturas viven en memoria y se reinician con el servidor.
- Los logs mostrados por la web se limitan a sus líneas más recientes; los archivos locales conservan el registro operativo.

## Limitaciones conocidas

- No hay reubicación general de tablas de punteros para scripts arbitrarios.
- La extracción ELF cubre cadenas candidatas conocidas, no todos los posibles textos del ejecutable.
- Los campos ELF no pueden crecer más allá de su espacio efectivo.
- El editor visual aún no ofrece comparación avanzada antes/después para texturas.
- TIM2 se parchea conservando la paleta y sólo para formatos indexados soportados.
- Host, puerto y rutas no son configurables por CLI o variables de entorno.
- La interfaz depende actualmente de recursos CDN.
- El selector de idioma UI no localiza todavía todas las plantillas.
- El número de procesos no paraleliza todas las etapas del build.
- La herramienta no proporciona una CLI independiente para importar o construir; esas operaciones se ejecutan desde la web.

## Desarrollo

Verificación recomendada antes de publicar cambios:

```bash
cargo fmt --all --check
cargo check --workspace
cargo test --workspace
cargo build --release --bin strawtraduccion
```

Pruebas por crate:

```bash
cargo test -p straw-core
cargo test -p straw-db
cargo test -p straw-web
```

## English overview

StrawTraduccion is a local Rust web application for translating, reviewing, and rebuilding *Strawberry Panic! Girls' School in Fullbloom* for PlayStation 2. It combines text extraction, capacity-aware translation editing, TIM2 image handling, binary patching, and ISO generation in one reproducible workflow.

The repository contains source code and documentation only. Original game files and generated builds must remain local and must be supplied by users from their own legitimate copy.

### Core capabilities

- Extract LZ77 scripts from `Data.bin`.
- Import script and ELF text into SQLite.
- Edit translations through a paginated web interface with FTS5 search and fit states.
- Rebuild UTF-16LE scripts using conservative `local-slack` or validated `shift-suffix` strategies.
- Patch Shift-JIS strings in `SLPS_256.11` without extending their effective fields.
- Inventory direct, compressed, and nested TIM2 resources.
- Group identical textures by deterministic content hash.
- Upload one edited PNG and propagate it to every matching texture target.
- Recalculate horizontal font metrics for modified donor glyphs.
- Build a patched `Data.bin` and inject it together with the ELF into a test ISO.

### Requirements and setup

Install a stable Rust toolchain and place the following user-provided files under `originales/`:

```text
originales/
├── Data.bin
├── SLPS_256.11
└── Strawberry_patched.iso
```

Run the application from the repository root:

```bash
./run_rust.sh
```

or:

```bash
cargo run --bin strawtraduccion
```

Open `http://127.0.0.1:8080` in a modern browser. The current interface loads Tailwind CSS and HTMX from CDNs, so an Internet connection is required for the complete UI.

### Recommended workflow

1. Use `/import` to extract scripts and populate `work/translation_manager.db`.
2. Translate and review entries through `/scripts` and `/search`.
3. Resolve capacity warnings and inspect build reports for skipped entries.
4. Generate a fresh texture inventory from `/textures` when image work is required.
5. Keep one working PNG per duplicate group, preserve its dimensions, and upload it through `/textures/upload`.
6. Run a complete, text-only, or image-only build from `/build`.
7. Validate `work/Strawberry_translated.iso` in a controlled environment.

### Binary safety model

The pipeline treats original files as immutable inputs and writes generated data under `work/` and `work_texturas/`. Full builds start from clean originals and regenerate script and texture intermediates.

Every binary transformation is capacity checked. Rebuilt LZ77 data is decompressed again for verification, ELF strings cannot exceed their effective fields, and ISO injection cannot extend the target image. Unsafe or oversized changes are reported instead of being written silently.

### Texture and duplicate policy

Duplicate groups are based on image type, dimensions, CLUT size, indexed image data, and original palette data. Equal dimensions alone do not make two textures duplicates.

Texture editing currently preserves the original palette and supports the indexed TIM2 formats handled by the project. Uploaded PNGs must retain their exact original dimensions. When one member of a duplicate group is uploaded, the application creates an explicit manifest entry and PNG copy for every matching target.

### Current limitations

- General pointer-table relocation is not implemented for arbitrary scripts.
- ELF extraction targets known candidate strings rather than every possible executable string.
- TIM2 patching preserves existing palettes and supported indexed formats.
- Host, port, and project paths are currently fixed.
- The interface still depends on CDN resources.
- Import and build operations are exposed through the local web application rather than a standalone CLI.

### Development checks

```bash
cargo fmt --all --check
cargo check --workspace
cargo test --workspace
cargo build --release --bin strawtraduccion
```

This is a free, non-commercial fan project. It does not distribute game images, executable files, archives, or generated ISOs. All rights to *Strawberry Panic!* and its original assets belong to their respective owners.

## Créditos

- Proyecto y traducción: [innovStudio](https://innovstudio.net/)
- Canal: [InnovatedSk en YouTube](https://www.youtube.com/@InnovatedSk)
- Desarrollo: [Saya-ya](https://github.com/Saya-ya)
- Página del proyecto: [Strawberry Panic! en innovStudio](https://innovstudio.net/proyectos/strawberry-panic)
