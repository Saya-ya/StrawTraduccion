# Rust Migration Status

Estado: migración principal completada.

El runtime anterior, la web anterior, tooling legado y configuraciones obsoletas fueron retirados del repositorio. El flujo soportado ahora es Rust-only.

## Cubierto En Rust

- Web local con Axum + Askama.
- SQLite con `sqlx` y FTS5.
- Settings.
- Listado/detalle de scripts.
- Edición inline con contador de bytes y capacidad.
- Búsqueda.
- Importación desde `Data.bin` y `.dec`.
- Importación de textos ELF.
- Export CSV temporal de build.
- Rebuild local-slack de scripts.
- Compresión/descompresión LZ77.
- FAT `Data.bin` y actualización de `size_field`.
- Parcheo ELF Shift-JIS.
- Inventario TIM2 directo/anidado.
- Export PNG de TIM2.
- Parcheo TIM2 `preserve_palette`.
- Inyección de texturas parcheadas.
- Generación de ISO.
- Inyección ELF en ISO.
- Build completo desde `/build`.
- Logs de build en vivo.
- Lock para evitar builds completos concurrentes.

## Pendiente Opcional

- Mejorar paralelización real del build usando el selector de procesos para pasos seguros.
- Añadir más tests de integración con fixtures pequeños para TIM2/ISO.
- Mejorar UI/UX según pruebas reales de traducción.
- Empaquetar binario release si se quiere distribución fuera del repo.

## Validación Manual

Ya se probó la ISO generada por Rust y el juego funciona correctamente.

## Comandos

```bash
./run_rust.sh
cargo fmt --all
cargo test --workspace
cargo check --workspace
```
