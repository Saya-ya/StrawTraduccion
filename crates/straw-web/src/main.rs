use std::net::SocketAddr;

use askama::Template;
use axum::{extract::State, response::Html, routing::get, Router};
use sqlx::SqlitePool;
use straw_db::{connect_path, get_setting, init_db, list_scripts, ScriptSummary, DEFAULT_DB_PATH};
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
