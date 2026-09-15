use std::{
    fs::OpenOptions,
    io::Write,
    net::SocketAddr,
    path::Path as FsPath,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

use askama::Template;
use axum::{
    extract::{DefaultBodyLimit, Multipart, Path, Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Redirect},
    routing::{get, post, put},
    Form, Router,
};
use sqlx::SqlitePool;
use straw_core::{
    build_iso_with_patched_data, copy_file_creating_parent, extract_lz77_scripts_to_dir,
    inject_elf_into_iso, inject_patched_texture_streams, patch_textures_from_manifest,
    patch_translated_elf, patch_translated_scripts, spanish_glyph_map, write_texture_inventory,
    TextureRecord,
};
use straw_db::{
    connect_path, export_translations_csv, get_script_detail_filtered, get_setting, get_text_entry,
    import_extracted_texts_with_progress, import_translations_from_db, init_db, list_scripts,
    recent_builds, record_build_step, search_text_entries_filtered, set_setting, translation_stats,
    update_text_entry_translation, BuildSummary, ScriptDetail, ScriptSummary, SearchResult,
    TextEntrySummary, TranslationStats, DEFAULT_DB_PATH,
};
use tower_http::services::ServeDir;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

const DATA_BIN_PATH: &str = "originales/Data.bin";
const ORIGINAL_ELF_PATH: &str = "originales/SLPS_256.11";
const SCRIPTS_OUT_DIR: &str = "work/scripts_extraidos";
const PATCHED_DATA_PATH: &str = "work/Data_patched.bin";
const ISO_OUT_PATH: &str = "work/Strawberry_translated.iso";
const BASE_ISO_PATH: &str = "originales/Strawberry_patched.iso";
const TRANSLATED_ELF_PATH: &str = "work/SLPS_256.11_translated";
const BUILD_CSV_PATH: &str = "work/build_temp/dialogo.csv";
const BUILD_LOG_PATH: &str = "work/build_temp/build.log";
const IMPORT_LOG_PATH: &str = "work/import_temp/import.log";
const IMPORT_DB_UPLOAD_PATH: &str = "work/import_temp/imported_translation_manager.db";
const MAX_UPLOAD_BYTES: usize = 512 * 1024 * 1024;
const TEXTURE_INVENTORY_DIR: &str = "work_texturas/output/all_textures";
const TEXTURE_MANIFEST_PATH: &str = "texturas/manifest.json";
const TEXTURE_PATCHED_DIR: &str = "work_texturas/patched";
const TEXTURE_LOG_PATH: &str = "work_texturas/texture.log";

#[derive(Clone)]
struct AppState {
    db: SqlitePool,
    build_running: Arc<AtomicBool>,
    import_running: Arc<AtomicBool>,
    texture_running: Arc<AtomicBool>,
}

struct BuildRunGuard(Arc<AtomicBool>);

impl Drop for BuildRunGuard {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate<'a> {
    title: &'a str,
    db_path: &'a str,
    ui_lang: &'a str,
    target_lang: &'a str,
}

#[derive(Template)]
#[template(path = "scripts.html")]
struct ScriptsTemplate {
    scripts: Vec<ScriptSummary>,
    scripts_count: usize,
    total_texts: i64,
    translated_texts: i64,
    percent_label: String,
}

#[derive(Template)]
#[template(path = "script_detail.html")]
struct ScriptDetailTemplate {
    detail: ScriptDetail,
    prev_page: i64,
    next_page: i64,
    status_filter: String,
    status_query: String,
}

#[derive(Template)]
#[template(path = "components/text_row.html")]
struct TextRowTemplate {
    text: TextEntrySummary,
}

#[derive(Template)]
#[template(path = "components/text_editor.html")]
struct TextEditorTemplate {
    text: TextEntrySummary,
}

#[derive(Template)]
#[template(path = "search.html")]
struct SearchTemplate {
    query: String,
    script_id: String,
    status_filter: String,
    results: Vec<SearchResult>,
    error: String,
}

#[derive(Template)]
#[template(path = "settings.html")]
struct SettingsTemplate {
    ui_lang: String,
    target_lang: String,
    message: String,
}

#[derive(Template)]
#[template(path = "import.html")]
struct ImportTemplate {
    data_bin_path: &'static str,
    scripts_out_dir: &'static str,
    data_bin_exists: bool,
    existing_dec_count: usize,
    import_running: bool,
    import_log: String,
    message: String,
    error: String,
}

#[derive(Template)]
#[template(path = "build.html")]
struct BuildTemplate {
    stats: TranslationStats,
    builds: Vec<BuildSummary>,
    data_bin_exists: bool,
    dec_count: usize,
    patched_data_exists: bool,
    iso_exists: bool,
    base_iso_exists: bool,
    original_elf_exists: bool,
    translated_elf_exists: bool,
    iso_out_path: &'static str,
    build_csv_path: &'static str,
    build_csv_exists: bool,
    texture_inventory_exists: bool,
    texture_manifest_exists: bool,
    build_running: bool,
    default_workers: usize,
    max_workers: usize,
    build_log: String,
    message: String,
    error: String,
}

#[derive(Template)]
#[template(path = "textures.html")]
struct TexturesTemplate {
    records: Vec<TextureRecord>,
    inventory_exists: bool,
    png_count: usize,
    data_bin_exists: bool,
    patched_data_exists: bool,
    texture_manifest_exists: bool,
    patched_streams_exist: bool,
    texture_running: bool,
    texture_log: String,
}

#[derive(Debug, serde::Deserialize)]
struct PageParams {
    page: Option<i64>,
    limit: Option<i64>,
    status: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct SearchParams {
    q: Option<String>,
    script_id: Option<String>,
    status: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct TextForm {
    translated_text: String,
}

#[derive(Debug, serde::Deserialize)]
struct SettingsForm {
    ui_lang: String,
    target_lang: String,
}

#[derive(Debug, serde::Deserialize)]
struct SettingsParams {
    saved: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct ImportParams {
    status: Option<String>,
    count: Option<usize>,
    scripts: Option<usize>,
    texts: Option<usize>,
    preserved: Option<usize>,
    translations: Option<usize>,
    started: Option<String>,
    error: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct BuildParams {
    exported: Option<usize>,
    prepared: Option<u64>,
    patched: Option<usize>,
    elf_patched: Option<usize>,
    iso: Option<u64>,
    elf: Option<u64>,
    textures: Option<usize>,
    texture_patches: Option<usize>,
    texture_injected: Option<usize>,
    full: Option<usize>,
    started: Option<u8>,
    error: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct FullBuildForm {
    build_type: String,
    workers: usize,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "straw_web=info,tower_http=info".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    print_startup_banner();

    let db = connect_path(DEFAULT_DB_PATH).await?;
    init_db(&db).await?;

    let app = Router::new()
        .route("/", get(index))
        .route("/health", get(health))
        .route("/scripts", get(scripts))
        .route("/scripts/:script_id", get(script_detail))
        .route("/search", get(search))
        .route("/textures", get(textures_page))
        .route("/textures/log", get(texture_log))
        .route("/textures/inventory", post(texture_inventory))
        .route("/textures/patch", post(patch_textures))
        .route("/textures/inject", post(inject_textures))
        .route("/import", get(import_page).post(run_import))
        .route("/import/db", post(import_db))
        .route("/import/log", get(import_log))
        .route(
            "/import/translations-db",
            post(import_translations_db_upload),
        )
        .route("/build", get(build_page))
        .route("/build/log", get(build_log))
        .route("/build/export-csv", post(export_build_csv))
        .route("/build/prepare-data", post(prepare_data_bin))
        .route("/build/patch-scripts", post(patch_scripts))
        .route("/build/patch-elf", post(patch_elf))
        .route("/build/build-iso", post(build_iso))
        .route("/build/inject-elf", post(inject_elf))
        .route("/build/texture-inventory", post(texture_inventory))
        .route("/build/patch-textures", post(patch_textures))
        .route("/build/inject-textures", post(inject_textures))
        .route("/build/run-full", post(run_full_build))
        .route("/settings", get(settings).post(save_settings))
        .route("/api/texts/:entry_id/edit", get(text_editor))
        .route("/api/texts/:entry_id/view", get(text_row))
        .route("/api/texts/:entry_id", put(update_text))
        .nest_service("/texture-assets", ServeDir::new(TEXTURE_INVENTORY_DIR))
        .layer(DefaultBodyLimit::max(MAX_UPLOAD_BYTES))
        .with_state(AppState {
            db,
            build_running: Arc::new(AtomicBool::new(false)),
            import_running: Arc::new(AtomicBool::new(false)),
            texture_running: Arc::new(AtomicBool::new(false)),
        });
    let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
    tracing::info!(%addr, "starting StrawTraduccion web server");
    println!("Servidor listo: http://127.0.0.1:8080");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

fn print_startup_banner() {
    println!("============================================================");
    println!("StrawTraduccion - servidor local Rust");
    println!("Abre: http://127.0.0.1:8080");
    println!("DB: {DEFAULT_DB_PATH}");
    println!("Originales requeridos: {DATA_BIN_PATH}, {ORIGINAL_ELF_PATH}, {BASE_ISO_PATH}");
    println!("Logs: {IMPORT_LOG_PATH}, {BUILD_LOG_PATH} y {TEXTURE_LOG_PATH}");
    println!("No cierres esta ventana mientras uses la aplicacion.");
    println!("============================================================");
}

async fn index(State(state): State<AppState>) -> Html<String> {
    let ui_lang: String = get_setting(&state.db, "ui_lang", "es".to_owned())
        .await
        .unwrap_or_else(|_| "es".to_owned());
    let target_lang: String = get_setting(&state.db, "target_lang", "es".to_owned())
        .await
        .unwrap_or_else(|_| "es".to_owned());

    let template = IndexTemplate {
        title: "StrawTraduccion",
        db_path: DEFAULT_DB_PATH,
        ui_lang: &ui_lang,
        target_lang: &target_lang,
    };
    Html(template.render().expect("index template renders"))
}

async fn health() -> &'static str {
    "ok"
}

async fn scripts(State(state): State<AppState>) -> Html<String> {
    let scripts = list_scripts(&state.db).await.unwrap_or_default();
    let total_texts = scripts.iter().map(|script| script.total_texts).sum();
    let translated_texts = scripts.iter().map(|script| script.translated_texts).sum();
    let percent = if total_texts > 0 {
        translated_texts as f64 / total_texts as f64 * 100.0
    } else {
        0.0
    };
    let scripts_count = scripts.len();

    let template = ScriptsTemplate {
        scripts,
        scripts_count,
        total_texts,
        translated_texts,
        percent_label: format!("{percent:.1}"),
    };
    Html(template.render().expect("scripts template renders"))
}

async fn script_detail(
    State(state): State<AppState>,
    Path(script_id): Path<i64>,
    Query(params): Query<PageParams>,
) -> impl IntoResponse {
    let page = params.page.unwrap_or(1);
    let limit = params.limit.unwrap_or(50);
    let status_filter = normalize_status_filter(params.status.as_deref());
    match get_script_detail_filtered(&state.db, script_id, page, limit, &status_filter).await {
        Ok(Some(detail)) => {
            let prev_page = (detail.page - 1).max(1);
            let next_page = (detail.page + 1).min(detail.total_pages);
            let status_query = status_filter_query(&status_filter);
            let template = ScriptDetailTemplate {
                detail,
                prev_page,
                next_page,
                status_filter,
                status_query,
            };
            Html(template.render().expect("script detail template renders")).into_response()
        }
        Ok(None) => (StatusCode::NOT_FOUND, "script not found").into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to load script: {err}"),
        )
            .into_response(),
    }
}

async fn search(State(state): State<AppState>, Query(params): Query<SearchParams>) -> Html<String> {
    let query = params.q.unwrap_or_default();
    let status_filter = normalize_status_filter(params.status.as_deref());
    let script_id = params
        .script_id
        .as_deref()
        .unwrap_or_default()
        .trim()
        .parse::<i64>()
        .ok();
    let (results, error) =
        match search_text_entries_filtered(&state.db, &query, 100, script_id, &status_filter).await
        {
            Ok(results) => (results, String::new()),
            Err(err) => (Vec::new(), format!("No se pudo buscar: {err}")),
        };
    let template = SearchTemplate {
        query,
        script_id: script_id.map(|id| id.to_string()).unwrap_or_default(),
        status_filter,
        results,
        error,
    };
    Html(template.render().expect("search template renders"))
}

async fn textures_page(State(state): State<AppState>) -> Html<String> {
    let inventory_path = FsPath::new(TEXTURE_INVENTORY_DIR).join("textures.json");
    let mut records = match tokio::fs::read_to_string(&inventory_path).await {
        Ok(contents) => serde_json::from_str::<Vec<TextureRecord>>(&contents).unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    let inventory_exists = !records.is_empty();
    let png_count = records
        .iter()
        .filter(|record| !record.png.is_empty())
        .count();
    records.sort_by(|a, b| {
        b.score_ui
            .cmp(&a.score_ui)
            .then_with(|| b.score_font.cmp(&a.score_font))
            .then_with(|| a.id.cmp(&b.id))
    });
    records.truncate(240);

    let template = TexturesTemplate {
        records,
        inventory_exists,
        png_count,
        data_bin_exists: FsPath::new(DATA_BIN_PATH).exists(),
        patched_data_exists: FsPath::new(PATCHED_DATA_PATH).exists(),
        texture_manifest_exists: FsPath::new(TEXTURE_MANIFEST_PATH).exists(),
        patched_streams_exist: FsPath::new(TEXTURE_PATCHED_DIR).exists(),
        texture_running: state.texture_running.load(Ordering::Acquire),
        texture_log: read_texture_log(),
    };
    Html(template.render().expect("textures template renders"))
}

async fn settings(
    State(state): State<AppState>,
    Query(params): Query<SettingsParams>,
) -> Html<String> {
    let ui_lang: String = get_setting(&state.db, "ui_lang", "es".to_owned())
        .await
        .unwrap_or_else(|_| "es".to_owned());
    let target_lang: String = get_setting(&state.db, "target_lang", "es".to_owned())
        .await
        .unwrap_or_else(|_| "es".to_owned());
    let message = if params.saved.as_deref() == Some("1") {
        "Configuracion guardada".to_owned()
    } else {
        String::new()
    };
    let template = SettingsTemplate {
        ui_lang,
        target_lang,
        message,
    };
    Html(template.render().expect("settings template renders"))
}

async fn import_page(
    State(state): State<AppState>,
    Query(params): Query<ImportParams>,
) -> Html<String> {
    let message = match params.status.as_deref() {
        Some("ok") => format!(
            "Extraccion completada: {} scripts LZ77 escritos",
            params.count.unwrap_or(0)
        ),
        Some("db") => format!(
            "SQLite importado: {} scripts, {} textos, {} traducciones preservadas",
            params.scripts.unwrap_or(0),
            params.texts.unwrap_or(0),
            params.preserved.unwrap_or(0)
        ),
        Some("translations") => format!(
            "Traducciones fusionadas desde DB externa: {} entradas actualizadas",
            params.translations.unwrap_or(0)
        ),
        _ if params.started.as_deref() == Some("extract") => {
            "Extraccion iniciada; el log se actualiza automaticamente".to_owned()
        }
        _ if params.started.as_deref() == Some("db") => {
            "Importacion a SQLite iniciada; el log se actualiza automaticamente".to_owned()
        }
        _ if params.started.as_deref() == Some("translations") => {
            "Fusion de DB traducida iniciada; el log se actualiza automaticamente".to_owned()
        }
        _ => String::new(),
    };
    let error = params.error.unwrap_or_default();
    let template = ImportTemplate {
        data_bin_path: DATA_BIN_PATH,
        scripts_out_dir: SCRIPTS_OUT_DIR,
        data_bin_exists: FsPath::new(DATA_BIN_PATH).exists(),
        existing_dec_count: count_dec_files(FsPath::new(SCRIPTS_OUT_DIR)),
        import_running: state.import_running.load(Ordering::Acquire),
        import_log: read_import_log(),
        message,
        error,
    };
    Html(template.render().expect("import template renders"))
}

async fn build_page(
    State(state): State<AppState>,
    Query(params): Query<BuildParams>,
) -> Html<String> {
    let stats = translation_stats(&state.db)
        .await
        .unwrap_or(TranslationStats {
            scripts: 0,
            text_entries: 0,
            translated_entries: 0,
            needs_shift_entries: 0,
        });
    let builds = recent_builds(&state.db, 10).await.unwrap_or_default();
    let template = BuildTemplate {
        stats,
        builds,
        data_bin_exists: FsPath::new(DATA_BIN_PATH).exists(),
        dec_count: count_dec_files(FsPath::new(SCRIPTS_OUT_DIR)),
        patched_data_exists: FsPath::new(PATCHED_DATA_PATH).exists(),
        iso_exists: FsPath::new(ISO_OUT_PATH).exists(),
        base_iso_exists: FsPath::new(BASE_ISO_PATH).exists(),
        original_elf_exists: FsPath::new(ORIGINAL_ELF_PATH).exists(),
        translated_elf_exists: FsPath::new(TRANSLATED_ELF_PATH).exists(),
        iso_out_path: ISO_OUT_PATH,
        build_csv_path: BUILD_CSV_PATH,
        build_csv_exists: FsPath::new(BUILD_CSV_PATH).exists(),
        texture_inventory_exists: FsPath::new(TEXTURE_INVENTORY_DIR)
            .join("textures.json")
            .exists(),
        texture_manifest_exists: FsPath::new(TEXTURE_MANIFEST_PATH).exists(),
        build_running: state.build_running.load(Ordering::Acquire),
        default_workers: default_workers(),
        max_workers: max_workers(),
        build_log: read_build_log(),
        message: params
            .exported
            .map(|count| format!("CSV exportado: {count} traducciones"))
            .or_else(|| {
                params.prepared.map(|bytes| {
                    format!(
                        "Data_patched.bin preparado: {} MB copiados",
                        bytes / 1024 / 1024
                    )
                })
            })
            .or_else(|| {
                params
                    .patched
                    .map(|count| format!("Scripts parcheados: {count}"))
            })
            .or_else(|| {
                params
                    .elf_patched
                    .map(|count| format!("Entradas ELF parcheadas: {count}"))
            })
            .or_else(|| {
                params
                    .iso
                    .map(|bytes| format!("ISO generada: {} MB escritos", bytes / 1024 / 1024))
            })
            .or_else(|| {
                params
                    .elf
                    .map(|bytes| format!("ELF inyectado: {bytes} bytes escritos"))
            })
            .or_else(|| {
                params
                    .textures
                    .map(|count| format!("Inventario TIM2 generado: {count} texturas"))
            })
            .or_else(|| {
                params
                    .texture_patches
                    .map(|count| format!("Parches de textura aplicados: {count}"))
            })
            .or_else(|| {
                params
                    .texture_injected
                    .map(|count| format!("Streams de textura inyectados: {count}"))
            })
            .or_else(|| {
                params
                    .full
                    .map(|count| format!("Build completo finalizado: {count} pasos ejecutados"))
            })
            .or_else(|| {
                params.started.map(|_| {
                    "Build completo iniciado; el log se actualiza automaticamente".to_owned()
                })
            })
            .unwrap_or_default(),
        error: params.error.unwrap_or_default(),
    };
    Html(template.render().expect("build template renders"))
}

async fn export_build_csv(State(state): State<AppState>) -> impl IntoResponse {
    append_build_log("Paso manual: exportar CSV iniciado");
    println!("[build] Exportando CSV a {BUILD_CSV_PATH}");
    match export_translations_csv(&state.db, BUILD_CSV_PATH, true).await {
        Ok(count) => {
            append_build_log(&format!("CSV exportado: {count} filas en {BUILD_CSV_PATH}"));
            println!("[build] CSV exportado: {count} filas");
            record_build_success(&state.db, "export csv", 20, "").await;
            Redirect::to(&format!("/build?exported={count}")).into_response()
        }
        Err(err) => {
            append_build_log(&format!("ERROR exportando CSV: {err}"));
            eprintln!("[build] ERROR exportando CSV: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn prepare_data_bin(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/build?error=missing_databin").into_response();
    }

    append_build_log(&format!(
        "Paso manual: copiando {DATA_BIN_PATH} a {PATCHED_DATA_PATH} ({})",
        file_size_label(DATA_BIN_PATH)
    ));
    println!("[build] Preparando Data_patched.bin");
    let result =
        tokio::task::spawn_blocking(|| copy_file_creating_parent(DATA_BIN_PATH, PATCHED_DATA_PATH))
            .await;

    match result {
        Ok(Ok(bytes)) => {
            append_build_log(&format!(
                "Data_patched.bin preparado: {} MB copiados",
                bytes / 1024 / 1024
            ));
            println!("[build] Data_patched.bin preparado: {bytes} bytes");
            record_build_success(&state.db, "prepare Data.bin", 35, "").await;
            Redirect::to(&format!("/build?prepared={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            append_build_log(&format!("ERROR preparando Data_patched.bin: {err}"));
            eprintln!("[build] ERROR preparando Data_patched.bin: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            append_build_log(&format!(
                "ERROR de tarea preparando Data_patched.bin: {err}"
            ));
            eprintln!("[build] ERROR de tarea preparando Data_patched.bin: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn patch_scripts(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(PATCHED_DATA_PATH).exists() {
        return Redirect::to("/build?error=missing_patched_data").into_response();
    }
    if !FsPath::new(BUILD_CSV_PATH).exists() {
        return Redirect::to("/build?error=missing_build_csv").into_response();
    }

    append_build_log(&format!(
        "Paso manual: parcheando scripts desde {BUILD_CSV_PATH}; .dec disponibles: {}",
        count_dec_files(FsPath::new(SCRIPTS_OUT_DIR))
    ));
    println!("[build] Parcheando scripts traducidos");
    let result = tokio::task::spawn_blocking(|| {
        let glyph_map = spanish_glyph_map();
        patch_translated_scripts(
            PATCHED_DATA_PATH,
            SCRIPTS_OUT_DIR,
            BUILD_CSV_PATH,
            Some(&glyph_map),
        )
    })
    .await;

    match result {
        Ok(Ok(report)) if report.errors.is_empty() => {
            append_build_log(&format!(
                "Scripts parcheados: {}/{} scripts, {}/{} filas aplicadas",
                report.scripts_patched,
                report.scripts_total,
                report.rows_applied,
                report.rows_total
            ));
            println!("[build] Scripts parcheados: {}", report.scripts_patched);
            record_build_success(&state.db, "patch scripts", 55, "").await;
            Redirect::to(&format!("/build?patched={}", report.scripts_patched)).into_response()
        }
        Ok(Ok(report)) => {
            append_build_log(&format!(
                "ERROR parcheando scripts: {}",
                report.errors.join("; ")
            ));
            Redirect::to(&format!(
                "/build?error={}",
                url_escape(&report.errors.join("; "))
            ))
            .into_response()
        }
        Ok(Err(err)) => {
            append_build_log(&format!("ERROR parcheando scripts: {err}"));
            eprintln!("[build] ERROR parcheando scripts: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            append_build_log(&format!("ERROR de tarea parcheando scripts: {err}"));
            eprintln!("[build] ERROR de tarea parcheando scripts: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn patch_elf(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(ORIGINAL_ELF_PATH).exists() {
        return Redirect::to("/build?error=missing_original_elf").into_response();
    }
    if !FsPath::new(BUILD_CSV_PATH).exists() {
        return Redirect::to("/build?error=missing_build_csv").into_response();
    }

    append_build_log(&format!(
        "Paso manual: parcheando ELF {ORIGINAL_ELF_PATH} -> {TRANSLATED_ELF_PATH}"
    ));
    println!("[build] Parcheando ELF traducido");
    let result = tokio::task::spawn_blocking(|| {
        let glyph_map = spanish_glyph_map();
        patch_translated_elf(
            ORIGINAL_ELF_PATH,
            TRANSLATED_ELF_PATH,
            BUILD_CSV_PATH,
            Some(&glyph_map),
        )
    })
    .await;

    match result {
        Ok(Ok(report)) => {
            append_build_log(&format!(
                "ELF parcheado: {} filas aplicadas, {} demasiado largas, {} omitidas, {} bytes escritos",
                report.rows_patched, report.skipped_too_large, report.skipped_other, report.bytes_written
            ));
            println!("[build] ELF parcheado: {} filas", report.rows_patched);
            record_build_success(&state.db, "patch ELF", 65, "").await;
            Redirect::to(&format!("/build?elf_patched={}", report.rows_patched)).into_response()
        }
        Ok(Err(err)) => {
            append_build_log(&format!("ERROR parcheando ELF: {err}"));
            eprintln!("[build] ERROR parcheando ELF: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            append_build_log(&format!("ERROR de tarea parcheando ELF: {err}"));
            eprintln!("[build] ERROR de tarea parcheando ELF: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn build_iso(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(BASE_ISO_PATH).exists() {
        return Redirect::to("/build?error=missing_base_iso").into_response();
    }
    if !FsPath::new(PATCHED_DATA_PATH).exists() {
        return Redirect::to("/build?error=missing_patched_data").into_response();
    }

    append_build_log(&format!(
        "Paso manual: generando ISO {ISO_OUT_PATH}; base {}, Data {}",
        file_size_label(BASE_ISO_PATH),
        file_size_label(PATCHED_DATA_PATH)
    ));
    println!("[build] Generando ISO traducida");
    let result = tokio::task::spawn_blocking(|| {
        build_iso_with_patched_data(BASE_ISO_PATH, PATCHED_DATA_PATH, ISO_OUT_PATH)
    })
    .await;

    match result {
        Ok(Ok(bytes)) => {
            append_build_log(&format!(
                "ISO generada: {bytes} bytes de Data.bin inyectados en {ISO_OUT_PATH}"
            ));
            println!("[build] ISO generada: {ISO_OUT_PATH}");
            record_build_success(&state.db, "build ISO", 85, ISO_OUT_PATH).await;
            Redirect::to(&format!("/build?iso={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            append_build_log(&format!("ERROR generando ISO: {err}"));
            eprintln!("[build] ERROR generando ISO: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            append_build_log(&format!("ERROR de tarea generando ISO: {err}"));
            eprintln!("[build] ERROR de tarea generando ISO: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn inject_elf(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(ISO_OUT_PATH).exists() {
        return Redirect::to("/build?error=missing_iso").into_response();
    }
    if !FsPath::new(ORIGINAL_ELF_PATH).exists() {
        return Redirect::to("/build?error=missing_original_elf").into_response();
    }
    if !FsPath::new(TRANSLATED_ELF_PATH).exists() {
        return Redirect::to("/build?error=missing_translated_elf").into_response();
    }

    append_build_log(&format!(
        "Paso manual: inyectando ELF {TRANSLATED_ELF_PATH} en {ISO_OUT_PATH}"
    ));
    println!("[build] Inyectando ELF en ISO");
    let result = tokio::task::spawn_blocking(|| {
        inject_elf_into_iso(ISO_OUT_PATH, ORIGINAL_ELF_PATH, TRANSLATED_ELF_PATH)
    })
    .await;

    match result {
        Ok(Ok(bytes)) => {
            append_build_log(&format!("ELF inyectado en ISO: {bytes} bytes escritos"));
            println!("[build] ELF inyectado: {bytes} bytes");
            record_build_success(&state.db, "inject ELF", 100, ISO_OUT_PATH).await;
            Redirect::to(&format!("/build?elf={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            append_build_log(&format!("ERROR inyectando ELF: {err}"));
            eprintln!("[build] ERROR inyectando ELF: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            append_build_log(&format!("ERROR de tarea inyectando ELF: {err}"));
            eprintln!("[build] ERROR de tarea inyectando ELF: {err}");
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn texture_inventory(State(state): State<AppState>) -> impl IntoResponse {
    let Ok(guard) = acquire_texture_guard(&state) else {
        return Redirect::to("/textures?error=texture_task_running").into_response();
    };
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/textures?error=missing_databin").into_response();
    }

    reset_texture_log();
    append_texture_log(&format!(
        "Paso manual: generando inventario TIM2 desde {DATA_BIN_PATH} hacia {TEXTURE_INVENTORY_DIR}"
    ));
    println!("[textures] Generando inventario TIM2");
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        let result = tokio::task::spawn_blocking(|| {
            write_texture_inventory(DATA_BIN_PATH, TEXTURE_INVENTORY_DIR)
        })
        .await;

        match result {
            Ok(Ok(records)) => {
                let with_png = records
                    .iter()
                    .filter(|record| !record.png.is_empty())
                    .count();
                let nested = records
                    .iter()
                    .filter(|record| record.nested_lz77_offset.is_some())
                    .count();
                append_texture_log(&format!(
                    "Inventario TIM2 generado: {} texturas, {} PNGs, {} con LZ77 anidado",
                    records.len(),
                    with_png,
                    nested
                ));
                println!(
                    "[textures] Inventario TIM2 generado: {} texturas",
                    records.len()
                );
                record_build_success(&db, "texture inventory", 15, "").await;
            }
            Ok(Err(err)) => {
                append_texture_log(&format!("ERROR generando inventario TIM2: {err}"));
                eprintln!("[textures] ERROR generando inventario TIM2: {err}");
            }
            Err(err) => {
                append_texture_log(&format!("ERROR de tarea generando inventario TIM2: {err}"));
                eprintln!("[textures] ERROR de tarea generando inventario TIM2: {err}");
            }
        }
    });

    Redirect::to("/textures?started=inventory").into_response()
}

async fn patch_textures(State(state): State<AppState>) -> impl IntoResponse {
    let Ok(guard) = acquire_texture_guard(&state) else {
        return Redirect::to("/textures?error=texture_task_running").into_response();
    };
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/textures?error=missing_databin").into_response();
    }
    if !FsPath::new(TEXTURE_MANIFEST_PATH).exists() {
        return Redirect::to("/textures?error=missing_texture_manifest").into_response();
    }

    reset_texture_log();
    append_texture_log(&format!(
        "Paso manual: parcheando texturas usando {TEXTURE_MANIFEST_PATH}; salida {TEXTURE_PATCHED_DIR}"
    ));
    println!("[textures] Parcheando texturas desde manifest");
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        let result = tokio::task::spawn_blocking(|| {
            patch_textures_from_manifest(DATA_BIN_PATH, TEXTURE_MANIFEST_PATH, TEXTURE_PATCHED_DIR)
        })
        .await;

        match result {
            Ok(Ok(report)) if report.errors.is_empty() => {
                append_texture_log(&format!(
                    "Texturas parcheadas: {} archivos procesados, {} parches aplicados, {} streams escritos",
                    report.files_processed, report.patches_applied, report.streams_written
                ));
                println!(
                    "[textures] Texturas parcheadas: {} parches",
                    report.patches_applied
                );
                record_build_success(&db, "patch textures", 70, "").await;
            }
            Ok(Ok(report)) => {
                append_texture_log(&format!(
                    "ERROR parcheando texturas: {}",
                    report.errors.join("; ")
                ));
            }
            Ok(Err(err)) => {
                append_texture_log(&format!("ERROR parcheando texturas: {err}"));
                eprintln!("[textures] ERROR parcheando texturas: {err}");
            }
            Err(err) => {
                append_texture_log(&format!("ERROR de tarea parcheando texturas: {err}"));
                eprintln!("[textures] ERROR de tarea parcheando texturas: {err}");
            }
        }
    });

    Redirect::to("/textures?started=patch").into_response()
}

async fn inject_textures(State(state): State<AppState>) -> impl IntoResponse {
    let Ok(guard) = acquire_texture_guard(&state) else {
        return Redirect::to("/textures?error=texture_task_running").into_response();
    };
    if !FsPath::new(PATCHED_DATA_PATH).exists() {
        return Redirect::to("/textures?error=missing_patched_data").into_response();
    }
    if !FsPath::new(TEXTURE_PATCHED_DIR).exists() {
        return Redirect::to("/textures?error=missing_patched_texture_streams").into_response();
    }

    reset_texture_log();
    append_texture_log(&format!(
        "Paso manual: inyectando streams de textura desde {TEXTURE_PATCHED_DIR} en {PATCHED_DATA_PATH}"
    ));
    println!("[textures] Inyectando streams de textura");
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        let result = tokio::task::spawn_blocking(|| {
            inject_patched_texture_streams(PATCHED_DATA_PATH, TEXTURE_PATCHED_DIR)
        })
        .await;

        match result {
            Ok(Ok(report)) if report.errors.is_empty() => {
                append_texture_log(&format!(
                    "Streams de textura inyectados: {}, bytes escritos: {}",
                    report.streams_injected, report.bytes_written
                ));
                println!(
                    "[textures] Streams de textura inyectados: {}",
                    report.streams_injected
                );
                record_build_success(&db, "inject textures", 75, "").await;
            }
            Ok(Ok(report)) => {
                append_texture_log(&format!(
                    "ERROR inyectando texturas: {}",
                    report.errors.join("; ")
                ));
            }
            Ok(Err(err)) => {
                append_texture_log(&format!("ERROR inyectando texturas: {err}"));
                eprintln!("[textures] ERROR inyectando texturas: {err}");
            }
            Err(err) => {
                append_texture_log(&format!("ERROR de tarea inyectando texturas: {err}"));
                eprintln!("[textures] ERROR de tarea inyectando texturas: {err}");
            }
        }
    });

    Redirect::to("/textures?started=inject").into_response()
}

async fn run_full_build(
    State(state): State<AppState>,
    Form(form): Form<FullBuildForm>,
) -> impl IntoResponse {
    let Ok(guard) = acquire_build_guard(&state) else {
        return Redirect::to("/build?error=build_already_running").into_response();
    };

    reset_build_log();
    append_build_log("Build completo iniciado");
    let build_type = normalize_build_type(&form.build_type);
    let workers = form.workers.clamp(1, max_workers());
    append_build_log(&format!(
        "Configuracion: tipo={build_type}, procesos={workers}"
    ));
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        append_build_log("Exportando CSV desde SQLite");
        match export_translations_csv(&db, BUILD_CSV_PATH, true).await {
            Ok(count) => append_build_log(&format!("CSV exportado correctamente: {count} filas")),
            Err(err) => {
                append_build_log(&format!("ERROR exportando CSV: {err}"));
                return;
            }
        }

        match tokio::task::spawn_blocking(move || run_full_build_steps(&build_type, workers)).await
        {
            Ok(Ok(steps)) => {
                append_build_log(&format!(
                    "Build completo finalizado correctamente: {} pasos ejecutados",
                    steps + 1
                ));
                record_build_success(&db, "full build", 100, ISO_OUT_PATH).await;
            }
            Ok(Err(err)) => append_build_log(&format!("ERROR en build completo: {err}")),
            Err(err) => append_build_log(&format!("ERROR de tarea build: {err}")),
        }
    });

    Redirect::to("/build?started=1").into_response()
}

fn acquire_build_guard(state: &AppState) -> Result<BuildRunGuard, ()> {
    state
        .build_running
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .map(|_| BuildRunGuard(state.build_running.clone()))
        .map_err(|_| ())
}

fn run_full_build_steps(build_type: &str, workers: usize) -> anyhow::Result<usize> {
    append_build_log("Validando prerequisitos");
    if !FsPath::new(DATA_BIN_PATH).exists() {
        anyhow::bail!("missing originales/Data.bin");
    }
    if !FsPath::new(BASE_ISO_PATH).exists() {
        anyhow::bail!("missing originales/Strawberry_patched.iso");
    }
    if !FsPath::new(ORIGINAL_ELF_PATH).exists() {
        anyhow::bail!("missing originales/SLPS_256.11");
    }
    if count_dec_files(FsPath::new(SCRIPTS_OUT_DIR)) == 0 {
        anyhow::bail!("missing extracted .dec scripts; run import first");
    }

    let mut steps = 0;
    append_build_log(&format!(
        "Procesos seleccionados: {workers} (los pasos criticos de Data.bin se ejecutan con escritura segura)"
    ));
    append_build_log(&format!(
        "Prerequisitos OK: Data {}, ISO base {}, ELF {}, .dec {}",
        file_size_label(DATA_BIN_PATH),
        file_size_label(BASE_ISO_PATH),
        file_size_label(ORIGINAL_ELF_PATH),
        count_dec_files(FsPath::new(SCRIPTS_OUT_DIR))
    ));
    append_build_log(&format!("Copiando {DATA_BIN_PATH} a {PATCHED_DATA_PATH}"));
    copy_file_creating_parent(DATA_BIN_PATH, PATCHED_DATA_PATH)?;
    append_build_log(&format!(
        "Data_patched.bin preparado: {}",
        file_size_label(PATCHED_DATA_PATH)
    ));
    steps += 1;

    let glyph_map = spanish_glyph_map();
    if build_type != "images" {
        append_build_log(&format!(
            "Parcheando scripts traducidos desde {BUILD_CSV_PATH}; .dec disponibles: {}",
            count_dec_files(FsPath::new(SCRIPTS_OUT_DIR))
        ));
        let script_report = patch_translated_scripts(
            PATCHED_DATA_PATH,
            SCRIPTS_OUT_DIR,
            BUILD_CSV_PATH,
            Some(&glyph_map),
        )?;
        if !script_report.errors.is_empty() {
            anyhow::bail!(script_report.errors.join("; "));
        }
        append_build_log(&format!(
            "Scripts parcheados: {}/{} scripts, {}/{} filas aplicadas",
            script_report.scripts_patched,
            script_report.scripts_total,
            script_report.rows_applied,
            script_report.rows_total
        ));
        steps += 1;

        append_build_log(&format!(
            "Parcheando ELF traducido: {ORIGINAL_ELF_PATH} -> {TRANSLATED_ELF_PATH}"
        ));
        let elf_report = patch_translated_elf(
            ORIGINAL_ELF_PATH,
            TRANSLATED_ELF_PATH,
            BUILD_CSV_PATH,
            Some(&glyph_map),
        )?;
        append_build_log(&format!(
            "Entradas ELF parcheadas: {}, demasiado largas: {}, omitidas: {}, bytes escritos: {}",
            elf_report.rows_patched,
            elf_report.skipped_too_large,
            elf_report.skipped_other,
            elf_report.bytes_written
        ));
        steps += 1;
    } else {
        append_build_log("Build tipo imagenes: omitiendo scripts y ELF traducido");
        copy_file_creating_parent(ORIGINAL_ELF_PATH, TRANSLATED_ELF_PATH)?;
    }

    if build_type != "texts" && FsPath::new(TEXTURE_MANIFEST_PATH).exists() {
        append_build_log(&format!(
            "Parcheando texturas desde {TEXTURE_MANIFEST_PATH}; salida {TEXTURE_PATCHED_DIR}"
        ));
        let texture_report = patch_textures_from_manifest(
            DATA_BIN_PATH,
            TEXTURE_MANIFEST_PATH,
            TEXTURE_PATCHED_DIR,
        )?;
        if !texture_report.errors.is_empty() {
            anyhow::bail!(texture_report.errors.join("; "));
        }
        append_build_log(&format!(
            "Texturas parcheadas: {} archivos procesados, {} parches aplicados, {} streams escritos",
            texture_report.files_processed,
            texture_report.patches_applied,
            texture_report.streams_written
        ));
        steps += 1;

        append_build_log(&format!(
            "Inyectando streams de textura desde {TEXTURE_PATCHED_DIR} en {PATCHED_DATA_PATH}"
        ));
        let inject_report = inject_patched_texture_streams(PATCHED_DATA_PATH, TEXTURE_PATCHED_DIR)?;
        if !inject_report.errors.is_empty() {
            anyhow::bail!(inject_report.errors.join("; "));
        }
        append_build_log(&format!(
            "Streams de textura inyectados: {}, bytes escritos: {}",
            inject_report.streams_injected, inject_report.bytes_written
        ));
        steps += 1;
    } else if build_type == "texts" {
        append_build_log("Build tipo textos: etapa de texturas omitida");
    } else {
        append_build_log("Sin texturas/manifest.json; etapa de texturas omitida");
    }

    append_build_log(&format!(
        "Generando ISO traducida {ISO_OUT_PATH} desde base {} y Data {}",
        file_size_label(BASE_ISO_PATH),
        file_size_label(PATCHED_DATA_PATH)
    ));
    let iso_bytes = build_iso_with_patched_data(BASE_ISO_PATH, PATCHED_DATA_PATH, ISO_OUT_PATH)?;
    append_build_log(&format!(
        "Data.bin inyectado en ISO: {iso_bytes} bytes; ISO final {}",
        file_size_label(ISO_OUT_PATH)
    ));
    steps += 1;

    append_build_log(&format!(
        "Inyectando ELF traducido {} en {ISO_OUT_PATH}",
        file_size_label(TRANSLATED_ELF_PATH)
    ));
    let elf_bytes = inject_elf_into_iso(ISO_OUT_PATH, ORIGINAL_ELF_PATH, TRANSLATED_ELF_PATH)?;
    append_build_log(&format!("ELF inyectado en ISO: {elf_bytes} bytes"));
    steps += 1;

    Ok(steps)
}

fn normalize_build_type(value: &str) -> String {
    match value {
        "texts" | "images" => value.to_owned(),
        _ => "full".to_owned(),
    }
}

fn max_workers() -> usize {
    std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(2)
        .clamp(1, 8)
}

fn default_workers() -> usize {
    max_workers().min(2).max(1)
}

fn file_size_label(path: &str) -> String {
    match std::fs::metadata(path) {
        Ok(metadata) => format!("{} MB", metadata.len() / 1024 / 1024),
        Err(_) => "no disponible".to_owned(),
    }
}

async fn record_build_success(db: &SqlitePool, step: &str, progress_pct: i64, iso_path: &str) {
    let _ = record_build_step(db, "success", "full", step, progress_pct, iso_path, "").await;
}

async fn run_import(State(state): State<AppState>) -> impl IntoResponse {
    let Ok(guard) = acquire_import_guard(&state) else {
        return Redirect::to("/import?error=import_already_running").into_response();
    };
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/import?error=missing_databin").into_response();
    }

    reset_import_log();
    append_import_log("Extraccion LZ77 iniciada");
    println!("[import] Extraccion LZ77 iniciada");
    tokio::spawn(async move {
        let _guard = guard;
        let before = count_dec_files(FsPath::new(SCRIPTS_OUT_DIR));
        append_import_log(&format!(
            "Leyendo {DATA_BIN_PATH} ({}) ; .dec existentes antes: {before}",
            file_size_label(DATA_BIN_PATH)
        ));
        let result = tokio::task::spawn_blocking(|| {
            extract_lz77_scripts_to_dir(DATA_BIN_PATH, SCRIPTS_OUT_DIR)
        })
        .await;
        match result {
            Ok(Ok(count)) => {
                let after = count_dec_files(FsPath::new(SCRIPTS_OUT_DIR));
                append_import_log(&format!(
                    "Extraccion completada: {count} scripts escritos; .dec actuales: {after}"
                ));
                println!("[import] Extraccion completada: {count} scripts escritos");
            }
            Ok(Err(err)) => {
                append_import_log(&format!("ERROR extrayendo scripts: {err}"));
                eprintln!("[import] ERROR extrayendo scripts: {err}");
            }
            Err(err) => {
                append_import_log(&format!("ERROR de tarea de extraccion: {err}"));
                eprintln!("[import] ERROR de tarea de extraccion: {err}");
            }
        }
    });

    Redirect::to("/import?started=extract").into_response()
}

async fn import_db(State(state): State<AppState>) -> impl IntoResponse {
    let Ok(guard) = acquire_import_guard(&state) else {
        return Redirect::to("/import?error=import_already_running").into_response();
    };
    if !FsPath::new(SCRIPTS_OUT_DIR).exists() {
        return Redirect::to("/import?error=missing_dec_scripts").into_response();
    }

    reset_import_log();
    append_import_log("Importacion a SQLite iniciada");
    println!("[import] Importacion a SQLite iniciada");
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        append_import_log(&format!("Leyendo scripts desde {SCRIPTS_OUT_DIR}"));
        match import_extracted_texts_with_progress(
            &db,
            SCRIPTS_OUT_DIR,
            ORIGINAL_ELF_PATH,
            |message| {
                append_import_log(message);
                println!("[import] {message}");
            },
        )
        .await
        {
            Ok(report) => {
                append_import_log(&format!(
                    "SQLite importado: {} scripts, {} textos, {} traducciones preservadas, {} textos ELF",
                    report.scripts_imported,
                    report.texts_imported,
                    report.translations_preserved,
                    report.elf_texts_imported
                ));
                println!(
                    "[import] SQLite importado: {} scripts, {} textos",
                    report.scripts_imported, report.texts_imported
                );
            }
            Err(err) => {
                append_import_log(&format!("ERROR importando a SQLite: {err}"));
                eprintln!("[import] ERROR importando a SQLite: {err}");
            }
        }
    });

    Redirect::to("/import?started=db").into_response()
}

async fn import_translations_db_upload(
    State(state): State<AppState>,
    mut multipart: Multipart,
) -> impl IntoResponse {
    let Ok(guard) = acquire_import_guard(&state) else {
        return Redirect::to("/import?error=import_already_running").into_response();
    };

    reset_import_log();
    append_import_log("Carga de DB traducida iniciada");
    let mut saved = false;
    while let Ok(Some(field)) = multipart.next_field().await {
        if field.name() != Some("database") {
            continue;
        }
        let file_name = field.file_name().unwrap_or("database").to_owned();
        match field.bytes().await {
            Ok(bytes) if !bytes.is_empty() => {
                if let Some(parent) = FsPath::new(IMPORT_DB_UPLOAD_PATH).parent() {
                    if let Err(err) = tokio::fs::create_dir_all(parent).await {
                        return Redirect::to(&format!(
                            "/import?error={}",
                            url_escape(&format!("failed to create upload dir: {err}"))
                        ))
                        .into_response();
                    }
                }
                if let Err(err) = tokio::fs::write(IMPORT_DB_UPLOAD_PATH, bytes).await {
                    return Redirect::to(&format!(
                        "/import?error={}",
                        url_escape(&format!("failed to save uploaded database: {err}"))
                    ))
                    .into_response();
                }
                append_import_log(&format!("DB subida: {file_name}"));
                saved = true;
                break;
            }
            Ok(_) => {}
            Err(err) => {
                return Redirect::to(&format!(
                    "/import?error={}",
                    url_escape(&format!("failed to read uploaded database: {err}"))
                ))
                .into_response();
            }
        }
    }

    if !saved {
        return Redirect::to("/import?error=missing_database_upload").into_response();
    }

    println!("[import] Fusion de DB traducida iniciada");
    let db = state.db.clone();
    tokio::spawn(async move {
        let _guard = guard;
        append_import_log(
            "Fusionando traducciones por source + script_id + offset + texto original",
        );
        match import_translations_from_db(&db, IMPORT_DB_UPLOAD_PATH).await {
            Ok(report) => {
                append_import_log(&format!(
                    "Fusion completada: {} importadas, {} sin coincidencia, {} sin cambios, {} filas fuente",
                    report.imported,
                    report.skipped_missing,
                    report.skipped_unchanged,
                    report.source_rows
                ));
                println!(
                    "[import] Fusion completada: {} traducciones importadas",
                    report.imported
                );
            }
            Err(err) => {
                append_import_log(&format!("ERROR fusionando DB traducida: {err}"));
                eprintln!("[import] ERROR fusionando DB traducida: {err}");
            }
        }
    });

    Redirect::to("/import?started=translations").into_response()
}

fn acquire_import_guard(state: &AppState) -> Result<BuildRunGuard, ()> {
    state
        .import_running
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .map(|_| BuildRunGuard(state.import_running.clone()))
        .map_err(|_| ())
}

fn acquire_texture_guard(state: &AppState) -> Result<BuildRunGuard, ()> {
    state
        .texture_running
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .map(|_| BuildRunGuard(state.texture_running.clone()))
        .map_err(|_| ())
}

fn read_build_log() -> String {
    read_log(BUILD_LOG_PATH)
}

fn read_import_log() -> String {
    read_log(IMPORT_LOG_PATH)
}

fn read_texture_log() -> String {
    read_log(TEXTURE_LOG_PATH)
}

fn read_log(path: &str) -> String {
    let Ok(contents) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let lines = contents.lines().collect::<Vec<_>>();
    let start = lines.len().saturating_sub(300);
    lines[start..].join("\n")
}

fn reset_build_log() {
    reset_log(BUILD_LOG_PATH);
}

fn reset_import_log() {
    reset_log(IMPORT_LOG_PATH);
}

fn reset_texture_log() {
    reset_log(TEXTURE_LOG_PATH);
}

fn reset_log(path: &str) {
    if let Some(parent) = FsPath::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, "");
}

fn append_build_log(message: &str) {
    append_log(BUILD_LOG_PATH, message);
}

fn append_import_log(message: &str) {
    append_log(IMPORT_LOG_PATH, message);
}

fn append_texture_log(message: &str) {
    append_log(TEXTURE_LOG_PATH, message);
}

fn append_log(path: &str, message: &str) {
    if let Some(parent) = FsPath::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let timestamp = build_log_timestamp();
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "[{timestamp}] {message}");
    }
}

fn build_log_timestamp() -> String {
    match std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
        Ok(duration) => format!("{}", duration.as_secs()),
        Err(_) => "0".to_owned(),
    }
}

async fn build_log() -> impl IntoResponse {
    read_build_log()
}

async fn import_log() -> impl IntoResponse {
    read_import_log()
}

async fn texture_log() -> impl IntoResponse {
    read_texture_log()
}

fn count_dec_files(path: &FsPath) -> usize {
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    entries
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "dec"))
        .count()
}

fn url_escape(value: &str) -> String {
    value
        .chars()
        .flat_map(|ch| match ch {
            'a'..='z' | 'A'..='Z' | '0'..='9' | '-' | '_' | '.' | '~' => vec![ch],
            ' ' => vec!['+'],
            _ => format!("%{:02X}", ch as u32).chars().collect(),
        })
        .collect()
}

fn normalize_status_filter(value: Option<&str>) -> String {
    match value.unwrap_or("all") {
        "translated" | "warning" | "needs_shift" | "untranslated" => {
            value.unwrap_or("all").to_owned()
        }
        _ => "all".to_owned(),
    }
}

fn status_filter_query(status_filter: &str) -> String {
    if status_filter == "all" {
        String::new()
    } else {
        format!("&status={status_filter}")
    }
}

async fn save_settings(
    State(state): State<AppState>,
    Form(form): Form<SettingsForm>,
) -> impl IntoResponse {
    let ui_lang = if matches!(form.ui_lang.as_str(), "es" | "en") {
        form.ui_lang
    } else {
        "es".to_owned()
    };
    let target_lang = if matches!(form.target_lang.as_str(), "es" | "en" | "custom") {
        form.target_lang
    } else {
        "es".to_owned()
    };

    if let Err(err) = set_setting(&state.db, "ui_lang", &ui_lang).await {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to save ui_lang: {err}"),
        )
            .into_response();
    }
    if let Err(err) = set_setting(&state.db, "target_lang", &target_lang).await {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to save target_lang: {err}"),
        )
            .into_response();
    }

    Redirect::to("/settings?saved=1").into_response()
}

async fn text_editor(
    State(state): State<AppState>,
    Path(entry_id): Path<i64>,
) -> impl IntoResponse {
    match get_text_entry(&state.db, entry_id).await {
        Ok(Some(text)) => Html(
            TextEditorTemplate { text }
                .render()
                .expect("text editor template renders"),
        )
        .into_response(),
        Ok(None) => (StatusCode::NOT_FOUND, "text not found").into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to load text: {err}"),
        )
            .into_response(),
    }
}

async fn text_row(State(state): State<AppState>, Path(entry_id): Path<i64>) -> impl IntoResponse {
    match get_text_entry(&state.db, entry_id).await {
        Ok(Some(text)) => Html(
            TextRowTemplate { text }
                .render()
                .expect("text row template renders"),
        )
        .into_response(),
        Ok(None) => (StatusCode::NOT_FOUND, "text not found").into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to load text: {err}"),
        )
            .into_response(),
    }
}

async fn update_text(
    State(state): State<AppState>,
    Path(entry_id): Path<i64>,
    Form(form): Form<TextForm>,
) -> impl IntoResponse {
    match update_text_entry_translation(&state.db, entry_id, &form.translated_text).await {
        Ok(Some(text)) => Html(
            TextRowTemplate { text }
                .render()
                .expect("text row template renders"),
        )
        .into_response(),
        Ok(None) => (StatusCode::NOT_FOUND, "text not found").into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("failed to update text: {err}"),
        )
            .into_response(),
    }
}
