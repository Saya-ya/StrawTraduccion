# Plan de migracion a Rust

## Decisiones confirmadas

- Nombre del proyecto: `StrawTraduccion`.
- Framework web: `axum`.
- Templates: `askama` por tipado fuerte.
- Estrategia: reemplazo total; no se publicara una version parcial.
- Plataforma inicial: Linux/WSL.
- Base de datos: mantener SQLite salvo que aparezca un bloqueo tecnico real.
- Producto final: web local. Todo debe poder hacerse desde el frontend, sin pedir al usuario ejecutar comandos manuales.
- Binario final: `strawtraduccion`.
- Puerto por defecto: `8080`.
- Frontend: se puede mejorar el look durante la migracion, manteniendo la funcionalidad actual.
- Tests: se pueden usar los archivos reales/copias presentes en el repo; tambien se podran crear fixtures reducidos si aceleran pruebas puntuales.
- Rust disponible en el entorno: `rustc 1.98.1`, `cargo 1.98.1`.

## Objetivo

Reescribir `StrawTraduccion` en Rust como una aplicacion web local completa que reemplace la version Python actual.

La version Rust debe cubrir:

- Importacion de archivos originales.
- Extraccion de textos y texturas.
- Gestion web de traducciones.
- Busqueda y edicion inline.
- Configuracion de idioma y glifos.
- Validacion de capacidad/fit.
- Build completo de ISO.
- Historial y estado de builds.
- Parcheo de scripts, ELF y texturas.

Python queda como referencia tecnica durante el desarrollo, pero no como runtime final.

## Arquitectura objetivo

Workspace Cargo dentro del repositorio actual:

```text
StrawTraduccion/
├── Cargo.toml
├── crates/
│   ├── straw-core/        # formatos binarios, LZSS, FAT, TIM2, glifos, fit checker
│   ├── straw-db/          # SQLite, migraciones, modelos y queries
│   └── straw-web/         # Axum, Askama, rutas HTMX, servicios web
├── migrations/            # migraciones SQL versionadas
├── templates/             # templates Askama o fuente equivalente
└── tests/fixtures/        # fixtures reducidos si hacen falta
```

No se plantea CLI como producto de usuario. Puede haber binarios auxiliares internos durante desarrollo si ayudan a testear, pero el objetivo operativo es web local.

Dependencias propuestas:

- Web: `axum`, `tower-http`, `tokio`.
- Templates: `askama`.
- SQLite: `sqlx` con migraciones.
- Serializacion: `serde`, `serde_json`, `csv`.
- Errores/logging: `anyhow`, `thiserror`, `tracing`, `tracing-subscriber`.
- Imagenes PNG: `image` si hace falta para TIM2/PNG.
- Tests: `tempfile`, `insta` si conviene snapshots.

## Estrategia

Aunque el resultado se publicara como reemplazo total, el desarrollo debe hacerse por capas para reducir riesgo:

1. Portar y probar primero logica pura en `straw-core`.
2. Portar DB y servicios en `straw-db`/`straw-web`.
3. Portar la web local completa.
4. Integrar build completo desde frontend.
5. Retirar Python cuando Rust cubra todo el flujo.

Los archivos actuales son copias utilizables, asi que se pueden usar para pruebas de integracion contra `originales/`, `work/`, `textos/`, `texturas/` y `Replacement/`.

## Fase 1: workspace y nucleo binario

Crear workspace Cargo y crate `straw-core`.

Portar primero:

- `tools/glyph_map.py`.
- `webapp/services/fit_checker.py`.
- `tools/datafat.py`.
- `tools/lz77.py`.

Pruebas:

- Glifos ES/EN/custom.
- Normalizacion de mapa custom `glifo -> caracter` y `caracter -> glifo`.
- Encoding UTF-16LE y Shift-JIS.
- Roundtrip LZSS: `decompress(compress(data)) == data`.
- Lectura FAT contra `originales/Data.bin` con conteos esperados.

Criterio de salida:

- `cargo test` pasa.
- Tests de integracion validan que FAT/LZSS producen resultados equivalentes a Python.

## Fase 2: scripts y traducciones

Estado: implementado en Rust para el flujo principal.

Portar logica de textos:

- `tools/script_rebuilder.py`.
- `tools/dialogue_order.py`.
- `traduccion_tools/extract_dialogue.py`.
- `traduccion_tools/apply_translation.py`.

Servicios internos esperados:

- Extraer scripts `.dec` desde `Data.bin`.
- Generar orden narrativo y CSV interno.
- Extraer textos ELF.
- Rebuild local-slack.
- Aplicar mapa de glifos.
- Recomprimir scripts.

Pruebas:

- CSV/orden equivalente al Python actual en campos clave.
- Reporte `needs_shift` equivalente.
- Scripts reconstruidos se descomprimen correctamente.
- ELF traducido conserva offsets y limites.

Criterio de salida:

- Rust puede extraer/importar/reconstruir los scripts soportados sin llamar a Python.

## Fase 3: texturas y TIM2

Estado: implementado en Rust para inventario, PNG, parcheo `preserve_palette` e inyeccion de streams parcheados.

Portar pipeline de texturas:

- `tools/tim2_parser.py`.
- `tools/tim2_png.py`.
- `tools/tim2_encode.py`.
- `tools/texture_catalog.py`.
- `tools/patch_texture.py`.
- `tools/extract_all_textures.py`.

Pruebas:

- Detectar los mismos TIM2 directos y anidados que Python.
- Generar catalogo equivalente.
- Aplicar `texturas/manifest.json`.
- Validar casos con `lz77_offset`.
- Reportar correctamente si una textura no cabe en slot.

Criterio de salida:

- Rust puede extraer catalogo de texturas y aplicar parches sin llamar a Python.

## Fase 4: SQLite y servicios

Mantener SQLite como almacenamiento final.

Tablas actuales a cubrir:

- `scripts`.
- `text_entries`.
- `settings`.
- `build_history`.
- FTS para busqueda.

Servicios:

- Import service.
- Settings service.
- Glyph map service.
- Fit checker batch.
- Build state/history.
- Build lock.
- Search service.

Decisiones tecnicas:

- Usar `sqlx`.
- Mantener compatibilidad con `work/translation_manager.db` si no complica demasiado.
- Si hay que cambiar esquema, crear migraciones que preserven traducciones y settings.

Criterio de salida:

- Rust puede abrir la DB actual, importar datos, preservar traducciones y consultar busqueda/progreso.

## Fase 5: web local con Axum y Askama

Reemplazar FastAPI/Jinja por `straw-web`.

Rutas principales:

- `GET /` redirige a `/scripts`.
- `GET /scripts`.
- `GET /scripts/{script_id}`.
- `GET /api/texts/{entry_id}/edit`.
- `PUT /api/texts/{entry_id}`.
- `GET /api/texts/{entry_id}/row`.
- `GET /search`.
- `GET /import`.
- `POST /import/run`.
- `GET /build`.
- `POST /build/run`.
- `GET /build/status`.
- `GET /settings`.
- `POST /settings/ui-lang`.
- `POST /settings/target-lang`.
- `GET /settings/glyphs`.
- `POST /settings/glyphs`.
- `GET /textures`.

Frontend:

- Mantener HTMX.
- Mantener Tailwind al inicio.
- Mantener la experiencia actual: edicion inline, Ctrl+Enter, paginacion, filtros y estado de build.
- Mejorar el look cuando aporte claridad, sin sacrificar velocidad ni funcionalidad.
- Escape HTML por defecto desde Askama.

Criterio de salida:

- Desde la web local se puede importar, traducir, buscar, configurar glifos, parchear texturas y lanzar builds.

## Fase 6: build completo desde frontend

Estado: implementado como accion `/build/run-full`; pendiente validacion de ISO final en PCSX2/hardware.

El build debe ejecutarse solo desde la web local.

Flujo requerido:

1. Exportar traducciones desde SQLite a estructura interna o CSV temporal.
2. Copiar `originales/Data.bin` a `work/Data_patched.bin`.
3. Rebuild/recompress paralelo de scripts traducidos.
4. Inyectar scripts en `Data_patched.bin`.
5. Aplicar traducciones ELF.
6. Aplicar parches de textura.
7. Inyectar streams de textura en `Data_patched.bin`.
8. Generar `work/Strawberry_translated.iso`.
9. Inyectar ELF traducido en la ISO.
10. Registrar `build_history`.

Reglas:

- Si hay errores parciales, el build debe fallar explicitamente.
- No debe marcar exito si la ISO se genero con contenido incompleto.
- El frontend debe mostrar logs y progreso suficiente para diagnosticar.

Criterio de salida:

- ISO generada por Rust bootea igual que la generada por Python.
- No se requiere terminal para el flujo de usuario.

## Fase 7: retiro de Python

Cuando Rust cubra todo:

Estado: pendiente. Python ya no es llamado desde `crates/`, pero sigue en el repo como referencia y tooling legado.

- Mover Python a documentacion legado o eliminarlo.
- Actualizar README con instalacion y ejecucion Rust.
- Reemplazar `run_webapp.py` por binario Rust.
- Eliminar `build_worker.py` y subprocess Python.
- Mantener solo archivos necesarios para datos, texturas, templates y docs.

## Orden recomendado

1. Crear workspace Cargo.
2. Crear `straw-core`.
3. Portar glifos y fit checker.
4. Portar LZSS/LZ77.
5. Portar FAT/Data.bin.
6. Portar script rebuilder.
7. Portar extraccion/importacion de textos.
8. Portar SQLite/services.
9. Portar web Axum/Askama.
10. Portar texturas/TIM2.
11. Integrar build completo desde frontend. Hecho.
12. Validar ISO final. Pendiente.
13. Retirar Python. Pendiente.

## Riesgos principales

- LZSS debe ser byte-exacto; errores pequenos pueden producir black screen.
- FAT tiene quirk: `size_field` de una fila describe el archivo anterior.
- Shift-JIS y UTF-16LE deben conservar offsets y terminadores.
- `local-slack` no mueve punteros; traducciones largas deben seguir marcando `needs_shift`.
- TIM2 anidados dentro de LZ77 requieren fixtures reales.
- Builds paralelos no deben corromper `Data_patched.bin`.
- Mantener SQLite es viable, pero hay que manejar bien migraciones y FTS.

## Decisiones finales de ejecucion

- El servidor local se ejecutara con el binario `strawtraduccion`.
- El puerto inicial sera `8080`, salvo conflicto local.
- La UI puede redisenarse/mejorarse durante la migracion.
- Las pruebas de integracion pueden usar `originales/`, `work/`, `textos/`, `texturas/` y demas archivos actuales porque son copias de trabajo disponibles en este repositorio.
- Se permitiran fixtures reducidos para tests rapidos, pero los tests de paridad importantes deben usar datos reales cuando sea necesario.
