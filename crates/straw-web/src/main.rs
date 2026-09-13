use std::net::SocketAddr;

use askama::Template;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{Html, IntoResponse},
    routing::{get, put},
    Form, Router,
};
use sqlx::SqlitePool;
use straw_core::{check_fit, spanish_glyph_map, FitStatus, TextSource};
use straw_db::{
    connect_path, get_script_detail, get_setting, get_text_entry, init_db, list_scripts,
    search_text_entries, update_text_entry_translation, ScriptDetail, ScriptSummary, SearchResult,
    TextEntrySummary, DEFAULT_DB_PATH,
};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

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
