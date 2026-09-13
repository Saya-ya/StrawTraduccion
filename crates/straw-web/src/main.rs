use std::{net::SocketAddr, path::Path as FsPath};

use askama::Template;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Redirect},
    routing::{get, post, put},
    Form, Router,
};
use sqlx::SqlitePool;
use straw_core::{
    build_iso_with_patched_data, check_fit, copy_file_creating_parent, extract_lz77_scripts_to_dir,
    inject_elf_into_iso, inject_patched_texture_streams, patch_textures_from_manifest,
    patch_translated_elf, patch_translated_scripts, spanish_glyph_map, write_texture_inventory,
    FitStatus, TextSource,
};
use straw_db::{
    connect_path, export_translations_csv, get_script_detail, get_setting, get_text_entry,
    import_extracted_texts, init_db, list_scripts, recent_builds, record_build_step,
    search_text_entries, set_setting, translation_stats, update_text_entry_translation,
    BuildSummary, ScriptDetail, ScriptSummary, SearchResult, TextEntrySummary, TranslationStats,
    DEFAULT_DB_PATH,
};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

const DATA_BIN_PATH: &str = "originales/Data.bin";
const ORIGINAL_ELF_PATH: &str = "originales/SLPS_256.11";
const SCRIPTS_OUT_DIR: &str = "work/scripts_extraidos";
const PATCHED_DATA_PATH: &str = "work/Data_patched.bin";
const ISO_OUT_PATH: &str = "work/Strawberry_translated.iso";
const BASE_ISO_PATH: &str = "originales/Strawberry_patched.iso";
const TRANSLATED_ELF_PATH: &str = "work/SLPS_256.11_translated";
const BUILD_CSV_PATH: &str = "work/build_temp/dialogo.csv";
const TEXTURE_INVENTORY_DIR: &str = "work_texturas/output/all_textures";
const TEXTURE_MANIFEST_PATH: &str = "texturas/manifest.json";
const TEXTURE_PATCHED_DIR: &str = "work_texturas/patched";

#[derive(Clone)]
struct AppState {
    db: SqlitePool,
}

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate<'a> {
    title: &'a str,
    status: &'a str,
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
    message: String,
    error: String,
}

#[derive(Debug, serde::Deserialize)]
struct PageParams {
    page: Option<i64>,
    limit: Option<i64>,
}

#[derive(Debug, serde::Deserialize)]
struct SearchParams {
    q: Option<String>,
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
    error: Option<String>,
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

    let db = connect_path(DEFAULT_DB_PATH).await?;
    init_db(&db).await?;

    let app = Router::new()
        .route("/", get(index))
        .route("/health", get(health))
        .route("/scripts", get(scripts))
        .route("/scripts/:script_id", get(script_detail))
        .route("/search", get(search))
        .route("/import", get(import_page).post(run_import))
        .route("/import/db", post(import_db))
        .route("/build", get(build_page))
        .route("/build/export-csv", post(export_build_csv))
        .route("/build/prepare-data", post(prepare_data_bin))
        .route("/build/patch-scripts", post(patch_scripts))
        .route("/build/patch-elf", post(patch_elf))
        .route("/build/build-iso", post(build_iso))
        .route("/build/inject-elf", post(inject_elf))
        .route("/build/texture-inventory", post(texture_inventory))
        .route("/build/patch-textures", post(patch_textures))
        .route("/build/inject-textures", post(inject_textures))
        .route("/settings", get(settings).post(save_settings))
        .route("/api/texts/:entry_id/edit", get(text_editor))
        .route("/api/texts/:entry_id", put(update_text))
        .with_state(AppState { db });
    let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
    tracing::info!(%addr, "starting StrawTraduccion web server");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
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
        status: "Rust web server connected to SQLite",
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
    match get_script_detail(&state.db, script_id, page, limit).await {
        Ok(Some(detail)) => {
            let prev_page = (detail.page - 1).max(1);
            let next_page = (detail.page + 1).min(detail.total_pages);
            let template = ScriptDetailTemplate {
                detail,
                prev_page,
                next_page,
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
    let (results, error) = match search_text_entries(&state.db, &query, 100).await {
        Ok(results) => (results, String::new()),
        Err(err) => (Vec::new(), format!("No se pudo buscar: {err}")),
    };
    let template = SearchTemplate {
        query,
        results,
        error,
    };
    Html(template.render().expect("search template renders"))
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

async fn import_page(Query(params): Query<ImportParams>) -> Html<String> {
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
        _ => String::new(),
    };
    let error = params.error.unwrap_or_default();
    let template = ImportTemplate {
        data_bin_path: DATA_BIN_PATH,
        scripts_out_dir: SCRIPTS_OUT_DIR,
        data_bin_exists: FsPath::new(DATA_BIN_PATH).exists(),
        existing_dec_count: count_dec_files(FsPath::new(SCRIPTS_OUT_DIR)),
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
            .unwrap_or_default(),
        error: params.error.unwrap_or_default(),
    };
    Html(template.render().expect("build template renders"))
}

async fn export_build_csv(State(state): State<AppState>) -> impl IntoResponse {
    match export_translations_csv(&state.db, BUILD_CSV_PATH, true).await {
        Ok(count) => {
            record_build_success(&state.db, "export csv", 20, "").await;
            Redirect::to(&format!("/build?exported={count}")).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn prepare_data_bin(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/build?error=missing_databin").into_response();
    }

    let result =
        tokio::task::spawn_blocking(|| copy_file_creating_parent(DATA_BIN_PATH, PATCHED_DATA_PATH))
            .await;

    match result {
        Ok(Ok(bytes)) => {
            record_build_success(&state.db, "prepare Data.bin", 35, "").await;
            Redirect::to(&format!("/build?prepared={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
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
            record_build_success(&state.db, "patch scripts", 55, "").await;
            Redirect::to(&format!("/build?patched={}", report.scripts_patched)).into_response()
        }
        Ok(Ok(report)) => Redirect::to(&format!(
            "/build?error={}",
            url_escape(&report.errors.join("; "))
        ))
        .into_response(),
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
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
            record_build_success(&state.db, "patch ELF", 65, "").await;
            Redirect::to(&format!("/build?elf_patched={}", report.rows_patched)).into_response()
        }
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
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

    let result = tokio::task::spawn_blocking(|| {
        build_iso_with_patched_data(BASE_ISO_PATH, PATCHED_DATA_PATH, ISO_OUT_PATH)
    })
    .await;

    match result {
        Ok(Ok(bytes)) => {
            record_build_success(&state.db, "build ISO", 85, ISO_OUT_PATH).await;
            Redirect::to(&format!("/build?iso={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
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

    let result = tokio::task::spawn_blocking(|| {
        inject_elf_into_iso(ISO_OUT_PATH, ORIGINAL_ELF_PATH, TRANSLATED_ELF_PATH)
    })
    .await;

    match result {
        Ok(Ok(bytes)) => {
            record_build_success(&state.db, "inject ELF", 100, ISO_OUT_PATH).await;
            Redirect::to(&format!("/build?elf={bytes}")).into_response()
        }
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn texture_inventory(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/build?error=missing_databin").into_response();
    }

    let result = tokio::task::spawn_blocking(|| {
        write_texture_inventory(DATA_BIN_PATH, TEXTURE_INVENTORY_DIR)
    })
    .await;

    match result {
        Ok(Ok(records)) => {
            record_build_success(&state.db, "texture inventory", 15, "").await;
            Redirect::to(&format!("/build?textures={}", records.len())).into_response()
        }
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn patch_textures(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/build?error=missing_databin").into_response();
    }
    if !FsPath::new(TEXTURE_MANIFEST_PATH).exists() {
        return Redirect::to("/build?error=missing_texture_manifest").into_response();
    }

    let result = tokio::task::spawn_blocking(|| {
        patch_textures_from_manifest(DATA_BIN_PATH, TEXTURE_MANIFEST_PATH, TEXTURE_PATCHED_DIR)
    })
    .await;

    match result {
        Ok(Ok(report)) if report.errors.is_empty() => {
            record_build_success(&state.db, "patch textures", 70, "").await;
            Redirect::to(&format!(
                "/build?texture_patches={}",
                report.patches_applied
            ))
            .into_response()
        }
        Ok(Ok(report)) => Redirect::to(&format!(
            "/build?error={}",
            url_escape(&report.errors.join("; "))
        ))
        .into_response(),
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn inject_textures(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(PATCHED_DATA_PATH).exists() {
        return Redirect::to("/build?error=missing_patched_data").into_response();
    }
    if !FsPath::new(TEXTURE_PATCHED_DIR).exists() {
        return Redirect::to("/build?error=missing_patched_texture_streams").into_response();
    }

    let result = tokio::task::spawn_blocking(|| {
        inject_patched_texture_streams(PATCHED_DATA_PATH, TEXTURE_PATCHED_DIR)
    })
    .await;

    match result {
        Ok(Ok(report)) if report.errors.is_empty() => {
            record_build_success(&state.db, "inject textures", 75, "").await;
            Redirect::to(&format!(
                "/build?texture_injected={}",
                report.streams_injected
            ))
            .into_response()
        }
        Ok(Ok(report)) => Redirect::to(&format!(
            "/build?error={}",
            url_escape(&report.errors.join("; "))
        ))
        .into_response(),
        Ok(Err(err)) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/build?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn record_build_success(db: &SqlitePool, step: &str, progress_pct: i64, iso_path: &str) {
    let _ = record_build_step(db, "success", "full", step, progress_pct, iso_path, "").await;
}

async fn run_import() -> impl IntoResponse {
    if !FsPath::new(DATA_BIN_PATH).exists() {
        return Redirect::to("/import?error=missing_databin").into_response();
    }

    let result =
        tokio::task::spawn_blocking(|| extract_lz77_scripts_to_dir(DATA_BIN_PATH, SCRIPTS_OUT_DIR))
            .await;

    match result {
        Ok(Ok(count)) => Redirect::to(&format!("/import?status=ok&count={count}")).into_response(),
        Ok(Err(err)) => {
            Redirect::to(&format!("/import?error={}", url_escape(&err.to_string()))).into_response()
        }
        Err(err) => {
            Redirect::to(&format!("/import?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
}

async fn import_db(State(state): State<AppState>) -> impl IntoResponse {
    if !FsPath::new(SCRIPTS_OUT_DIR).exists() {
        return Redirect::to("/import?error=missing_dec_scripts").into_response();
    }

    match import_extracted_texts(&state.db, SCRIPTS_OUT_DIR, ORIGINAL_ELF_PATH).await {
        Ok(report) => Redirect::to(&format!(
            "/import?status=db&scripts={}&texts={}&preserved={}",
            report.scripts_imported, report.texts_imported, report.translations_preserved
        ))
        .into_response(),
        Err(err) => {
            Redirect::to(&format!("/import?error={}", url_escape(&err.to_string()))).into_response()
        }
    }
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

async fn update_text(
    State(state): State<AppState>,
    Path(entry_id): Path<i64>,
    Form(form): Form<TextForm>,
) -> impl IntoResponse {
    let existing = match get_text_entry(&state.db, entry_id).await {
        Ok(Some(entry)) => entry,
        Ok(None) => return (StatusCode::NOT_FOUND, "text not found").into_response(),
        Err(err) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to load text: {err}"),
            )
                .into_response()
        }
    };

    let source = if existing.source == "SCRIPT" {
        TextSource::Script
    } else {
        TextSource::Elf
    };
    let fallback_capacity = match source {
        TextSource::Script => existing.original_text.encode_utf16().count() * 2 + 2,
        TextSource::Elf => existing.original_text.len(),
    };
    let capacity = existing
        .segment_capacity
        .max(fallback_capacity as i64)
        .max(1) as usize;
    let glyph_map = spanish_glyph_map();
    let fit = check_fit(&form.translated_text, source, capacity, Some(&glyph_map));
    let fit_status = match fit.status {
        FitStatus::Unchecked => "unchecked",
        FitStatus::Ok => "ok",
        FitStatus::Tight => "tight",
        FitStatus::NeedsShift => "needs_shift",
    };

    match update_text_entry_translation(
        &state.db,
        entry_id,
        &form.translated_text,
        fit_status,
        fit.status == FitStatus::NeedsShift,
    )
    .await
    {
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
