use std::net::SocketAddr;

use askama::Template;
use axum::{response::Html, routing::get, Router};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate<'a> {
    title: &'a str,
    status: &'a str,
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

    let app = Router::new().route("/", get(index));
    let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
    tracing::info!(%addr, "starting StrawTraduccion web server");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index() -> Html<String> {
    let template = IndexTemplate {
        title: "StrawTraduccion",
        status: "Rust migration started",
    };
    Html(template.render().expect("index template renders"))
}
