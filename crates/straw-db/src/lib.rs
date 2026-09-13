use std::path::Path;

use anyhow::{Context, Result};
use serde::{de::DeserializeOwned, Serialize};
use sqlx::{sqlite::SqlitePoolOptions, Row, SqlitePool};

pub const DEFAULT_DB_PATH: &str = "work/translation_manager.db";

pub async fn connect(database_url: &str) -> Result<SqlitePool> {
    SqlitePoolOptions::new()
        .max_connections(1)
        .connect(database_url)
        .await
        .with_context(|| format!("failed to connect to SQLite database {database_url}"))
}

pub async fn connect_path(path: impl AsRef<Path>) -> Result<SqlitePool> {
    let path = path.as_ref();
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    connect(&format!("sqlite://{}", path.display())).await
}

pub async fn init_db(pool: &SqlitePool) -> Result<()> {
    create_schema(pool).await?;
    seed_default_settings(pool).await?;
    rebuild_fts_if_empty(pool).await?;
    Ok(())
}

pub async fn get_setting<T>(pool: &SqlitePool, key: &str, default: T) -> Result<T>
where
    T: DeserializeOwned,
{
    let row = sqlx::query("SELECT value FROM settings WHERE key = ?")
        .bind(key)
        .fetch_optional(pool)
        .await?;

    let Some(row) = row else {
        return Ok(default);
    };
    let value: Option<String> = row.try_get("value")?;
    let Some(value) = value.filter(|value| !value.is_empty()) else {
        return Ok(default);
    };

    match serde_json::from_str(&value) {
        Ok(parsed) => Ok(parsed),
        Err(_) => Ok(default),
    }
}

pub async fn set_setting<T>(pool: &SqlitePool, key: &str, value: &T) -> Result<()>
where
    T: Serialize + ?Sized,
{
    let serialized = serde_json::to_string(value)?;
    sqlx::query(
        "INSERT INTO settings (key, value) VALUES (?, ?) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    )
    .bind(key)
    .bind(serialized)
    .execute(pool)
    .await?;
    Ok(())
}

async fn create_schema(pool: &SqlitePool) -> Result<()> {
    let statements = [
        r#"
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY,
            source VARCHAR(10) DEFAULT 'SCRIPT',
            script_type VARCHAR(30) DEFAULT '',
            variant VARCHAR(1) DEFAULT '',
            offset_in_bin INTEGER DEFAULT 0,
            size_in_bin INTEGER DEFAULT 0,
            slot_capacity INTEGER DEFAULT 0,
            is_supported BOOLEAN DEFAULT 0,
            total_texts INTEGER DEFAULT 0,
            translated_texts INTEGER DEFAULT 0,
            total_sections INTEGER DEFAULT 0
        )
        "#,
        r#"
        CREATE TABLE IF NOT EXISTS text_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER NOT NULL,
            source VARCHAR(10) DEFAULT 'SCRIPT',
            byte_offset INTEGER NOT NULL,
            section_id INTEGER DEFAULT 0,
            section_order INTEGER DEFAULT 0,
            original_text TEXT NOT NULL,
            translated_text TEXT DEFAULT '',
            original_bytes INTEGER DEFAULT 0,
            segment_start INTEGER DEFAULT 0,
            segment_capacity INTEGER DEFAULT 0,
            is_translated BOOLEAN DEFAULT 0,
            needs_shift BOOLEAN DEFAULT 0,
            fit_status VARCHAR(20) DEFAULT 'unchecked',
            locked_by VARCHAR(50) DEFAULT '',
            locked_at DATETIME DEFAULT NULL,
            updated_at DATETIME DEFAULT NULL,
            FOREIGN KEY(script_id) REFERENCES scripts(id)
        )
        "#,
        "CREATE INDEX IF NOT EXISTS ix_script_offset ON text_entries (script_id, byte_offset)",
        "CREATE INDEX IF NOT EXISTS ix_script_section ON text_entries (script_id, section_id, section_order)",
        "CREATE INDEX IF NOT EXISTS ix_translated ON text_entries (is_translated)",
        r#"
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(50) PRIMARY KEY,
            value TEXT DEFAULT ''
        )
        "#,
        r#"
        CREATE TABLE IF NOT EXISTS build_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME DEFAULT NULL,
            status VARCHAR(20) DEFAULT 'running',
            build_type VARCHAR(20) DEFAULT 'full',
            script_id INTEGER DEFAULT NULL,
            iso_path VARCHAR(500) DEFAULT '',
            error_log TEXT DEFAULT '',
            step VARCHAR(200) DEFAULT '',
            progress_pct INTEGER DEFAULT 0
        )
        "#,
        r#"
        CREATE VIRTUAL TABLE IF NOT EXISTS text_entries_fts USING fts5(
            original_text, translated_text,
            content='text_entries',
            content_rowid='id'
        )
        "#,
        r#"
        CREATE TRIGGER IF NOT EXISTS text_entries_ai AFTER INSERT ON text_entries BEGIN
            INSERT INTO text_entries_fts(rowid, original_text, translated_text)
            VALUES (new.id, new.original_text, new.translated_text);
        END
        "#,
        r#"
        CREATE TRIGGER IF NOT EXISTS text_entries_ad AFTER DELETE ON text_entries BEGIN
            INSERT INTO text_entries_fts(text_entries_fts, rowid, original_text, translated_text)
            VALUES ('delete', old.id, old.original_text, old.translated_text);
        END
        "#,
        r#"
        CREATE TRIGGER IF NOT EXISTS text_entries_au AFTER UPDATE ON text_entries BEGIN
            INSERT INTO text_entries_fts(text_entries_fts, rowid, original_text, translated_text)
            VALUES ('delete', old.id, old.original_text, old.translated_text);
            INSERT INTO text_entries_fts(rowid, original_text, translated_text)
            VALUES (new.id, new.original_text, new.translated_text);
        END
        "#,
    ];

    for statement in statements {
        sqlx::query(statement).execute(pool).await?;
    }
    Ok(())
}

async fn seed_default_settings(pool: &SqlitePool) -> Result<()> {
    set_default_setting(pool, "ui_lang", "es").await?;
    set_default_setting(pool, "target_lang", "es").await?;
    set_default_setting(pool, "custom_glyph_map", &serde_json::json!({})).await?;
    Ok(())
}

async fn set_default_setting<T>(pool: &SqlitePool, key: &str, value: &T) -> Result<()>
where
    T: Serialize + ?Sized,
{
    let serialized = serde_json::to_string(value)?;
    sqlx::query("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)")
        .bind(key)
        .bind(serialized)
        .execute(pool)
        .await?;
    Ok(())
}

async fn rebuild_fts_if_empty(pool: &SqlitePool) -> Result<()> {
    let count: i64 = sqlx::query("SELECT COUNT(*) AS count FROM text_entries_fts")
        .fetch_one(pool)
        .await?
        .try_get("count")?;
    if count == 0 {
        sqlx::query(
            r#"
            INSERT INTO text_entries_fts(rowid, original_text, translated_text)
            SELECT id, original_text, translated_text FROM text_entries
            "#,
        )
        .execute(pool)
        .await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[tokio::test]
    async fn init_creates_default_settings() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        let ui_lang: String = get_setting(&pool, "ui_lang", "missing".to_owned())
            .await
            .unwrap();
        let target_lang: String = get_setting(&pool, "target_lang", "missing".to_owned())
            .await
            .unwrap();
        let custom: serde_json::Value = get_setting(&pool, "custom_glyph_map", json!(null))
            .await
            .unwrap();

        assert_eq!(ui_lang, "es");
        assert_eq!(target_lang, "es");
        assert_eq!(custom, json!({}));
    }

    #[tokio::test]
    async fn settings_roundtrip_json_values() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        set_setting(&pool, "test_json", &json!({ "a": 1, "b": [2] }))
            .await
            .unwrap();
        let value: serde_json::Value = get_setting(&pool, "test_json", json!({})).await.unwrap();

        assert_eq!(value, json!({ "a": 1, "b": [2] }));
    }

    #[tokio::test]
    async fn fts_triggers_track_text_entries() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        sqlx::query("INSERT INTO scripts (id) VALUES (1)")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query(
            "INSERT INTO text_entries (script_id, byte_offset, original_text, translated_text) \
             VALUES (1, 16, 'original', 'traducido')",
        )
        .execute(&pool)
        .await
        .unwrap();

        let count: i64 = sqlx::query(
            "SELECT COUNT(*) AS count FROM text_entries_fts WHERE text_entries_fts MATCH 'traducido'",
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .try_get("count")
        .unwrap();

        assert_eq!(count, 1);
    }
}
