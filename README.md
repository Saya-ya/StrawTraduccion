# StrawTraduccion

Herramienta local en Rust para traducir, revisar imagenes y reconstruir *Strawberry Panic! Girls' School in Fullbloom* de PS2.

Este repositorio contiene la app web local y el pipeline tecnico del proyecto de traduccion. No incluye datos del juego.

[innovStudio](https://innovstudio.net/) | [YouTube](https://www.youtube.com/@InnovatedSk) | [Proyecto Strawberry Panic](https://innovstudio.net/proyectos/strawberry-panic)

![innovStudio](https://innovstudio.net/images/banner.png)

## Estado

El flujo principal esta migrado a Rust y funciona desde una web local:

- Importacion de scripts LZ77 desde `Data.bin`.
- Importacion de textos a SQLite.
- Editor web con filtros, busqueda FTS y contador de bytes.
- Fit checker para marcar textos que no caben (`needs_shift`).
- Parcheo de scripts y ELF.
- Inventario TIM2 con PNGs exportados.
- Catalogo de texturas, duplicados por hash y subida de PNGs editados.
- Parcheo de texturas TIM2 directas y anidadas en LZ77.
- Generacion de `Data_patched.bin` e ISO final.
- Logs en vivo para importacion, build y texturas.

El proyecto sigue en desarrollo. Algunas partes del juego todavia requieren revision manual o mejoras tecnicas antes de considerarse completamente automatizadas.

## Aviso Legal

- Proyecto fan gratuito y sin fines de lucro.
- No vender, comercializar ni redistribuir como producto comercial.
- Requiere archivos extraidos de tu propio disco original.
- Este repositorio no incluye ni enlaza ROMs, ISOs, `Data.bin`, ELF ni otros datos protegidos.
- Los derechos de *Strawberry Panic!* pertenecen a sus respectivos propietarios.

## Requisitos

- Rust stable toolchain.
- Navegador moderno.
- Archivos originales colocados localmente en `originales/`:

```text
originales/Data.bin
originales/SLPS_256.11
originales/Strawberry_patched.iso
```

`Strawberry_patched.iso` es la ISO base sobre la que se inyecta `Data_patched.bin` y el ELF traducido.

## Inicio Rapido

```bash
./run_rust.sh
```

O directamente:

```bash
cargo run --bin strawtraduccion
```

Abrir en el navegador:

```text
http://127.0.0.1:8080
```

Para compilar release:

```bash
cargo build --release --bin strawtraduccion
```

El binario queda en:

```text
target/release/strawtraduccion
```

## Flujo Recomendado

1. Abrir `/import`.
2. Extraer scripts LZ77 desde `originales/Data.bin`.
3. Importar textos a SQLite.
4. Traducir desde `/scripts` o buscar pendientes desde `/search`.
5. Revisar textos `needs_shift` y warnings de espacio.
6. Abrir `/textures` si se quieren revisar o parchear imagenes.
7. Generar inventario TIM2.
8. Revisar `/textures/catalog` y `/textures/duplicates`.
9. Subir PNGs editados desde `/textures/upload` o desde el catalogo.
10. Abrir `/build`.
11. Ejecutar build completo o pasos manuales.
12. Probar `work/Strawberry_translated.iso`.

## Pantallas Principales

| Ruta | Uso |
|---|---|
| `/` | Resumen y accesos principales. |
| `/import` | Extraccion de scripts, importacion a SQLite y fusion de DB traducida. |
| `/scripts` | Lista de scripts y avance de traduccion. |
| `/scripts/:id` | Editor por script con filtros y paginacion. |
| `/search` | Busqueda global o por estado (`untranslated`, `warning`, `needs_shift`). |
| `/textures` | Panel de tareas de imagenes y log. |
| `/textures/catalog` | Catalogo completo de TIM2/PNGs. |
| `/textures/duplicates` | Duplicados detectados por hash canonico. |
| `/textures/upload` | Subida de PNGs editados y actualizacion de manifest. |
| `/build` | Build completo, pasos manuales, logs e historial. |
| `/settings` | Configuracion persistente en SQLite. |

## Textos y Rebuild

El juego guarda textos principalmente en dos lugares:

| Archivo | Contenido | Encoding | Estado |
|---|---|---|---|
| `Data.bin` | Dialogos de scripts LZ77 | UTF-16LE | Extraido, editable y parcheable. |
| `SLPS_256.11` | Menus, sistema, descripciones | Shift-JIS | Parcheo parcial soportado. |

El rebuild actual es conservador:

- Reemplaza texto dentro del espacio local disponible.
- No mueve punteros globales.
- Si una traduccion no cabe, se marca como `needs_shift`.
- El editor muestra contador estimado de bytes y capacidad.

Atajos utiles en el editor:

- `Ctrl+Enter`: guardar.
- `Esc`: cancelar edicion.
- `/`: enfocar busqueda dentro del script.

## Imagenes y TIM2

La seccion de texturas analiza TIM2 dentro de `Data.bin`, incluyendo imagenes dentro de streams LZ77 anidados.

Tambien extrae el recurso LZ77 de fuentes referenciado por el puntero de la
cabecera en `0x0C`. Este recurso esta antes del primer archivo de la FAT y no
aparece en esa tabla. En los originales del proyecto esta en `0x60000` y contiene
59 hojas TIM2, incluida la fuente cirilica de 256x256 usada por el juego.

- Se identifica con el **ID reservado `4294967295`** (`u32::MAX`), no con un ID
  real de la FAT. `fat_row = 27411` es un marcador de recurso fuera de FAT.
- La hoja cirilica es `ID_4294967295_T001_P00_256x256.png`.
- En el catalogo y la subida se puede buscar **fuentes** o **cabecera**.
- Conserva el mismo formato de JSON/CSV, nombres de PNG, `texture_hash`,
  deteccion de duplicados y manifest que el resto de texturas.
- El build resuelve de nuevo el puntero desde el Data original. Al inyectar,
  conserva la cabecera y la FAT; usa el tamano del propio stream LZ77 y comprueba
  que quepa antes del primer archivo FAT.
- Cuando se parchea una hoja de este recurso, el mapa de glifos activo identifica
  los slots donantes. Si el alfa del slot cambio frente al original, el build
  ajusta automaticamente su coordenada X y ancho al contenido visible, con un
  pixel de margen por lado. Y, alto y pagina se conservan para no alterar la
  linea base. El ajuste nunca se expande fuera del rectangulo donante original,
  por lo que no puede invadir el siguiente glifo.
- Las metricas se actualizan sobre las entradas existentes: no se agregan
  caracteres ni se modifica la estructura de la tabla. Los alias que apuntan al
  mismo donante comparten dibujo y metrica.

Para actualizar un inventario anterior, ejecuta **Generar inventario** en
Texturas. Subir un PNG prepara el manifest; el parcheo y la ISO se generan en
Build.

El idioma `Personalizado` acepta en Configuracion un mapa JSON en formato
`caracter deseado -> slot donante`, por ejemplo `{"ã":"Г","ç":"Д"}`. Ese mismo
mapa se usa para codificar textos y ajustar las metricas de fuente.

El inventario genera:

```text
work_texturas/output/all_textures/textures.json
work_texturas/output/all_textures/textures.csv
work_texturas/output/all_textures/png/*.png
```

Cada textura nueva incluye un `texture_hash` calculado sobre datos canonicos:

- tipo de imagen
- ancho/alto
- datos de imagen
- paleta/CLUT
- conteo de colores

Ese hash permite detectar duplicados. Si subes una imagen editada para una textura con duplicados, la app crea/copIa las entradas necesarias en `texturas/manifest.json` para aplicar el mismo PNG a todas las copias.

Ejemplo de PNG anidado:

```text
ID_15111_L000050_T003_P00_176x288.png
```

`L000050` indica offset de LZ77 anidado dentro del contenedor original.

## Build

El build puede ejecutarse completo o por partes.

Tipos de build:

| Tipo | Hace |
|---|---|
| Completo | Textos + ELF + texturas + ISO. |
| Solo textos | Omite texturas. |
| Solo texturas | Exige `texturas/manifest.json` y omite parcheo de scripts. |

Salida principal:

```text
work/Strawberry_translated.iso
```

Logs:

```text
work/import_temp/import.log
work/build_temp/build.log
work_texturas/texture.log
```

## Arquitectura

```text
StrawTraduccion/
├── crates/
│   ├── straw-core      # formatos binarios, FAT, LZ77, TIM2, scripts, ELF, ISO
│   ├── straw-db        # SQLite, FTS5, settings, import/export
│   └── straw-web       # Axum + Askama + Tailwind CDN
├── originales/         # archivos originales locales, no distribuidos
├── work/               # archivos generados de build/import
├── work_texturas/      # inventario TIM2, PNGs exportados y streams parcheados
├── texturas/           # PNGs editados + manifest local
├── Replacement/        # reemplazos locales/PCSX2 si se usan
├── docs/               # documentacion adicional
├── run_rust.sh         # launcher local
├── Cargo.toml
└── Cargo.lock
```

Stack principal:

| Capa | Tecnologia |
|---|---|
| Backend local | Rust + Axum |
| Templates | Askama |
| Estilos | Tailwind CSS via CDN |
| Base de datos | SQLite + sqlx + FTS5 |
| Binarios PS2 | Rust en `straw-core` |
| Texturas | TIM2 + PNG |

## Detalles Tecnicos Importantes

- `Data.bin` contiene decenas de miles de entradas internas.
- La FAT del juego tiene una peculiaridad: el campo de tamaño de una fila describe la entrada anterior. La logica esta centralizada en `straw-core::datafat`.
- LZ77 usa header de 12 bytes. Usar 16 bytes rompe la descompresion.
- Scripts: UTF-16LE con terminador.
- ELF: Shift-JIS.
- El caracter `＠` se usa como glifo especial tipo corazon; el encoder puede mapear variantes como `@`, `♥`, `♡`, `❤`.
- Las inyecciones en ISO/Data validan capacidad para evitar extender archivos accidentalmente.
- El catalogo TIM2 puede ser pesado; por eso esta separado del panel principal de texturas.

## Pendientes Tecnicos

Esta lista es intencionalmente explicita para que el estado real del proyecto sea claro.

- Rebuild con movimiento de punteros para textos que no caben en `local-slack`.
- Analisis mas completo de scripts no dialogo.
- Cobertura total de textos ELF/menus.
- Mejor edicion visual de texturas, incluyendo preview antes/despues.
- Validacion visual de paletas TIM2 al subir PNGs.
- Generacion automatica mas inteligente de manifest para casos no duplicados.
- Mejor empaquetado Windows con instalador o release portable.
- UI offline sin depender de Tailwind CDN.
- Mas tests con muestras reales reducidas de TIM2/LZ77/FAT.
- Documentacion tecnica mas profunda de la FAT, opcodes y formatos internos.

## Verificacion de Desarrollo

Antes de publicar cambios:

```bash
cargo fmt --all --check
cargo check --workspace
cargo test --workspace
cargo build --release --bin strawtraduccion
```

Comandos utiles:

```bash
cargo run --bin strawtraduccion
cargo test -p straw-core
cargo test -p straw-db
```

## Datos Generados e Ignorados

Estos directorios/archivos se consideran locales o regenerables:

```text
originales/
work/
work_texturas/
texturas/*.png
texturas/uploads/
texturas/manifest.json
```

No deben subirse datos protegidos ni builds completos del juego.

## Creditos y Enlaces

- Proyecto y traduccion: [innovStudio](https://innovstudio.net/)
- Canal: [InnovatedSk en YouTube](https://www.youtube.com/@InnovatedSk)
- GitHub: [Saya-ya](https://github.com/Saya-ya)
- Proyecto: [Strawberry Panic! en innovStudio](https://innovstudio.net/proyectos/strawberry-panic)

## Licencia / Uso

Codigo del repositorio bajo la licencia declarada en el proyecto. La traduccion y herramientas son para uso fan, gratuito y educativo. No se distribuyen archivos del juego.
