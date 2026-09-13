use std::path::Path;

use anyhow::{Context, Result};
use serde::{de::DeserializeOwned, Serialize};
use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    Row, SqlitePool,
};

pub const DEFAULT_DB_PATH: &str = "work/translation_manager.db";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScriptSummary {
    pub id: i64,
    pub source: String,
    pub script_type: String,
    pub variant: String,
    pub is_supported: bool,
    pub total_texts: i64,
    pub translated_texts: i64,
    pub total_sections: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextEntrySummary {
    pub id: i64,
    pub script_id: i64,
    pub source: String,
    pub byte_offset: i64,
    pub byte_offset_hex: String,
    pub section_id: i64,
    pub section_order: i64,
    pub original_text: String,
    pub translated_text: String,
    pub is_translated: bool,
    pub needs_shift: bool,
    pub fit_status: String,
    pub segment_capacity: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScriptDetail {
    pub script: ScriptSummary,
    pub texts: Vec<TextEntrySummary>,
    pub total: i64,
    pub page: i64,
    pub limit: i64,
    pub total_pages: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SearchResult {
    pub id: i64,
    pub script_id: i64,
    pub byte_offset: i64,
    pub byte_offset_hex: String,
    pub section_id: i64,
    pub section_order: i64,
    pub original_text: String,
    pub translated_text: String,
    pub is_translated: bool,
    pub needs_shift: bool,
}

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
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true);
    SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(options)
        .await
        .with_context(|| format!("failed to connect to SQLite database {}", path.display()))
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

pub async fn list_scripts(pool: &SqlitePool) -> Result<Vec<ScriptSummary>> {
    let rows = sqlx::query(
        r#"
        SELECT id, source, script_type, variant, is_supported,
               total_texts, translated_texts, total_sections
        FROM scripts
        ORDER BY id
        "#,
    )
    .fetch_all(pool)
    .await?;

    rows.into_iter()
        .map(|row| {
            Ok(ScriptSummary {
                id: row.try_get("id")?,
                source: row
                    .try_get::<Option<String>, _>("source")?
                    .unwrap_or_default(),
                script_type: row
                    .try_get::<Option<String>, _>("script_type")?
                    .unwrap_or_default(),
                variant: row
                    .try_get::<Option<String>, _>("variant")?
                    .unwrap_or_default(),
                is_supported: row.try_get::<i64, _>("is_supported")? != 0,
                total_texts: row.try_get::<Option<i64>, _>("total_texts")?.unwrap_or(0),
                translated_texts: row
                    .try_get::<Option<i64>, _>("translated_texts")?
                    .unwrap_or(0),
                total_sections: row
                    .try_get::<Option<i64>, _>("total_sections")?
                    .unwrap_or(0),
            })
        })
        .collect()
}

pub async fn get_script(pool: &SqlitePool, script_id: i64) -> Result<Option<ScriptSummary>> {
    let row = sqlx::query(
        r#"
        SELECT id, source, script_type, variant, is_supported,
               total_texts, translated_texts, total_sections
        FROM scripts
        WHERE id = ?
        "#,
    )
    .bind(script_id)
    .fetch_optional(pool)
    .await?;

    row.map(script_from_row).transpose()
}

pub async fn get_script_detail(
    pool: &SqlitePool,
    script_id: i64,
    page: i64,
    limit: i64,
) -> Result<Option<ScriptDetail>> {
    let Some(script) = get_script(pool, script_id).await? else {
        return Ok(None);
    };

    let page = page.max(1);
    let limit = limit.clamp(1, 200);
    let offset = (page - 1) * limit;

    let total: i64 = sqlx::query("SELECT COUNT(*) AS count FROM text_entries WHERE script_id = ?")
        .bind(script_id)
        .fetch_one(pool)
        .await?
        .try_get("count")?;

    let rows = sqlx::query(
        r#"
        SELECT id, script_id, source, byte_offset, section_id, section_order,
               original_text, translated_text, is_translated, needs_shift,
               fit_status, segment_capacity
        FROM text_entries
        WHERE script_id = ?
        ORDER BY section_id, section_order
        LIMIT ? OFFSET ?
        "#,
    )
    .bind(script_id)
    .bind(limit)
    .bind(offset)
    .fetch_all(pool)
    .await?;

    let texts = rows
        .into_iter()
        .map(text_entry_from_row)
        .collect::<Result<Vec<_>>>()?;
    let total_pages = if total > 0 {
        (total + limit - 1) / limit
    } else {
        1
    };

    Ok(Some(ScriptDetail {
        script,
        texts,
        total,
        page,
        limit,
        total_pages,
    }))
}

pub async fn get_text_entry(pool: &SqlitePool, entry_id: i64) -> Result<Option<TextEntrySummary>> {
    let row = sqlx::query(
        r#"
        SELECT id, script_id, source, byte_offset, section_id, section_order,
               original_text, translated_text, is_translated, needs_shift,
               fit_status, segment_capacity
        FROM text_entries
        WHERE id = ?
        "#,
    )
    .bind(entry_id)
    .fetch_optional(pool)
    .await?;

    row.map(text_entry_from_row).transpose()
}

pub async fn update_text_entry_translation(
    pool: &SqlitePool,
    entry_id: i64,
    translated_text: &str,
    fit_status: &str,
    needs_shift: bool,
) -> Result<Option<TextEntrySummary>> {
    let Some(existing) = get_text_entry(pool, entry_id).await? else {
        return Ok(None);
    };
    let is_translated = !translated_text.trim().is_empty();

    sqlx::query(
        r#"
        UPDATE text_entries
        SET translated_text = ?,
            is_translated = ?,
            fit_status = ?,
            needs_shift = ?,
            locked_by = '',
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        "#,
    )
    .bind(translated_text)
    .bind(if is_translated { 1 } else { 0 })
    .bind(fit_status)
    .bind(if needs_shift { 1 } else { 0 })
    .bind(entry_id)
    .execute(pool)
    .await?;

    refresh_script_translated_count(pool, existing.script_id).await?;
    get_text_entry(pool, entry_id).await
}

pub async fn search_text_entries(
    pool: &SqlitePool,
    query: &str,
    limit: i64,
) -> Result<Vec<SearchResult>> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(Vec::new());
    }
    let limit = limit.clamp(1, 200);

    let rows = sqlx::query(
        r#"
        SELECT te.id, te.script_id, te.byte_offset, te.section_id, te.section_order,
               te.original_text, te.translated_text, te.is_translated, te.needs_shift
        FROM text_entries_fts fts
        JOIN text_entries te ON te.id = fts.rowid
        WHERE text_entries_fts MATCH ?
        ORDER BY te.script_id, te.section_id, te.section_order
        LIMIT ?
        "#,
    )
    .bind(query)
    .bind(limit)
    .fetch_all(pool)
    .await?;

    rows.into_iter()
        .map(|row| {
            let byte_offset: i64 = row.try_get("byte_offset")?;
            Ok(SearchResult {
                id: row.try_get("id")?,
                script_id: row.try_get("script_id")?,
                byte_offset,
                byte_offset_hex: format!("0x{byte_offset:05X}"),
                section_id: row.try_get::<Option<i64>, _>("section_id")?.unwrap_or(0),
                section_order: row.try_get::<Option<i64>, _>("section_order")?.unwrap_or(0),
                original_text: row
                    .try_get::<Option<String>, _>("original_text")?
                    .unwrap_or_default(),
                translated_text: row
                    .try_get::<Option<String>, _>("translated_text")?
                    .unwrap_or_default(),
                is_translated: row.try_get::<i64, _>("is_translated")? != 0,
                needs_shift: row.try_get::<i64, _>("needs_shift")? != 0,
            })
        })
        .collect()
}

async fn refresh_script_translated_count(pool: &SqlitePool, script_id: i64) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE scripts
        SET translated_texts = (
            SELECT COUNT(*) FROM text_entries
            WHERE script_id = ? AND is_translated = 1
        )
        WHERE id = ?
        "#,
    )
    .bind(script_id)
    .bind(script_id)
    .execute(pool)
    .await?;
    Ok(())
}

fn script_from_row(row: sqlx::sqlite::SqliteRow) -> Result<ScriptSummary> {
    Ok(ScriptSummary {
        id: row.try_get("id")?,
        source: row
            .try_get::<Option<String>, _>("source")?
            .unwrap_or_default(),
        script_type: row
            .try_get::<Option<String>, _>("script_type")?
            .unwrap_or_default(),
        variant: row
            .try_get::<Option<String>, _>("variant")?
            .unwrap_or_default(),
        is_supported: row.try_get::<i64, _>("is_supported")? != 0,
        total_texts: row.try_get::<Option<i64>, _>("total_texts")?.unwrap_or(0),
        translated_texts: row
            .try_get::<Option<i64>, _>("translated_texts")?
            .unwrap_or(0),
        total_sections: row
            .try_get::<Option<i64>, _>("total_sections")?
            .unwrap_or(0),
    })
}

fn text_entry_from_row(row: sqlx::sqlite::SqliteRow) -> Result<TextEntrySummary> {
    let byte_offset: i64 = row.try_get("byte_offset")?;
    Ok(TextEntrySummary {
        id: row.try_get("id")?,
        script_id: row.try_get("script_id")?,
        source: row
            .try_get::<Option<String>, _>("source")?
            .unwrap_or_default(),
        byte_offset,
        byte_offset_hex: format!("0x{byte_offset:05X}"),
        section_id: row.try_get::<Option<i64>, _>("section_id")?.unwrap_or(0),
        section_order: row.try_get::<Option<i64>, _>("section_order")?.unwrap_or(0),
        original_text: row
            .try_get::<Option<String>, _>("original_text")?
            .unwrap_or_default(),
        translated_text: row
            .try_get::<Option<String>, _>("translated_text")?
            .unwrap_or_default(),
        is_translated: row.try_get::<i64, _>("is_translated")? != 0,
        needs_shift: row.try_get::<i64, _>("needs_shift")? != 0,
        fit_status: row
            .try_get::<Option<String>, _>("fit_status")?
            .unwrap_or_else(|| "unchecked".to_owned()),
        segment_capacity: row
            .try_get::<Option<i64>, _>("segment_capacity")?
            .unwrap_or(0),
    })
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

    #[tokio::test]
    async fn lists_scripts_in_id_order() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        sqlx::query(
            "INSERT INTO scripts (id, source, script_type, variant, is_supported, total_texts, translated_texts, total_sections) \
             VALUES (2, 'SCRIPT', 'B', 'x', 1, 10, 4, 3), \
                    (1, 'ELF', 'A', '', 0, 2, 1, 1)",
        )
        .execute(&pool)
        .await
        .unwrap();

        let scripts = list_scripts(&pool).await.unwrap();
        assert_eq!(scripts.len(), 2);
        assert_eq!(scripts[0].id, 1);
        assert_eq!(scripts[1].id, 2);
        assert!(scripts[1].is_supported);
        assert_eq!(scripts[1].translated_texts, 4);
    }

    #[tokio::test]
    async fn updating_text_refreshes_script_count() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        sqlx::query("INSERT INTO scripts (id, total_texts, translated_texts) VALUES (1, 1, 0)")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query(
            "INSERT INTO text_entries (script_id, source, byte_offset, original_text, translated_text, is_translated) \
             VALUES (1, 'SCRIPT', 16, 'original', '', 0)",
        )
        .execute(&pool)
        .await
        .unwrap();

        let updated = update_text_entry_translation(&pool, 1, "traducido", "ok", false)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(updated.translated_text, "traducido");
        assert!(updated.is_translated);

        let script = get_script(&pool, 1).await.unwrap().unwrap();
        assert_eq!(script.translated_texts, 1);
    }

    #[tokio::test]
    async fn search_uses_fts_table() {
        let pool = connect("sqlite::memory:").await.unwrap();
        init_db(&pool).await.unwrap();

        sqlx::query("INSERT INTO scripts (id) VALUES (1)")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query(
            "INSERT INTO text_entries (script_id, byte_offset, original_text, translated_text, is_translated) \
             VALUES (1, 16, 'strawberry original', 'panic traducido', 1)",
        )
        .execute(&pool)
        .await
        .unwrap();

        let results = search_text_entries(&pool, "panic", 20).await.unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].script_id, 1);
        assert_eq!(results[0].translated_text, "panic traducido");
    }
}
