use std::{
    fs,
    io::{Seek, SeekFrom, Write},
    path::Path,
};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::{copy_file_creating_parent, encode_game_sjis, GlyphMap};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PatchElfReport {
    pub rows_processed: usize,
    pub rows_patched: usize,
    pub skipped_too_large: usize,
    pub skipped_other: usize,
    pub bytes_written: usize,
}

#[derive(Debug, Deserialize)]
struct TranslationRow {
    source: String,
    offset: String,
    original_text: String,
    translated_text: String,
}

pub fn patch_translated_elf(
    original_elf: impl AsRef<Path>,
    translated_elf: impl AsRef<Path>,
    csv_path: impl AsRef<Path>,
    glyph_map: Option<&GlyphMap>,
) -> Result<PatchElfReport> {
    let original_elf = original_elf.as_ref();
    let translated_elf = translated_elf.as_ref();
    let csv_path = csv_path.as_ref();

    if !original_elf.exists() {
        bail!("original ELF not found: {}", original_elf.display());
    }
    if !csv_path.exists() {
        bail!("build CSV not found: {}", csv_path.display());
    }

    let original_size = fs::metadata(original_elf)?.len();
    let needs_copy = match fs::metadata(translated_elf) {
        Ok(metadata) => metadata.len() != original_size,
        Err(_) => true,
    };
    if needs_copy {
        copy_file_creating_parent(original_elf, translated_elf)?;
    }

    let mut reader = csv::Reader::from_path(csv_path)
        .with_context(|| format!("failed to read {}", csv_path.display()))?;
    let rows = reader
        .deserialize::<TranslationRow>()
        .collect::<std::result::Result<Vec<_>, _>>()?;

    let mut report = PatchElfReport {
        rows_processed: 0,
        rows_patched: 0,
        skipped_too_large: 0,
        skipped_other: 0,
        bytes_written: 0,
    };
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(translated_elf)
        .with_context(|| format!("failed to open {}", translated_elf.display()))?;

    for row in rows {
        if row.source != "ELF" || row.translated_text.trim().is_empty() {
            continue;
        }
        report.rows_processed += 1;

        let Some(offset) = parse_hex_offset(&row.offset) else {
            report.skipped_other += 1;
            continue;
        };
        let new_bytes = encode_game_sjis(row.translated_text.trim(), glyph_map);
        let original_bytes = encode_game_sjis(&row.original_text, None);
        if new_bytes.len() > original_bytes.len() {
            report.skipped_too_large += 1;
            continue;
        }

        file.seek(SeekFrom::Start(offset))?;
        file.write_all(&new_bytes)?;
        let padding = original_bytes.len() - new_bytes.len();
        if padding > 0 {
            file.write_all(&vec![0x20; padding])?;
        }
        report.rows_patched += 1;
        report.bytes_written += original_bytes.len();
    }

    file.flush()?;
    Ok(report)
}

fn parse_hex_offset(value: &str) -> Option<u64> {
    let value = value.trim();
    let value = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .unwrap_or(value);
    u64::from_str_radix(value, 16).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn patches_elf_rows_from_csv() {
        let temp = tempfile::tempdir().unwrap();
        let original = temp.path().join("SLPS_256.11");
        let translated = temp.path().join("SLPS_256.11_translated");
        let csv = temp.path().join("dialogo.csv");

        fs::write(&original, b"HEADERHello!TAIL").unwrap();
        fs::write(
            &csv,
            "source,file_id,offset,original_text,translated_text\r\nELF,ELF,0x6,Hello!,Bye\r\nSCRIPT,1,0x0,a,b\r\n",
        )
        .unwrap();

        let report = patch_translated_elf(&original, &translated, &csv, None).unwrap();
        assert_eq!(report.rows_processed, 1);
        assert_eq!(report.rows_patched, 1);
        let patched = fs::read(&translated).unwrap();
        assert_eq!(&patched[6..12], b"Bye   ");
    }

    #[test]
    fn reports_skipped_elf_rows_without_modifying_file() {
        let temp = tempfile::tempdir().unwrap();
        let original = temp.path().join("SLPS_256.11");
        let translated = temp.path().join("SLPS_256.11_translated");
        let csv = temp.path().join("dialogo.csv");

        fs::write(&original, b"HEADERHiTAIL").unwrap();
        fs::write(
            &csv,
            "source,file_id,offset,original_text,translated_text\r\n\
             ELF,ELF,not_hex,Hi,Ok\r\n\
             ELF,ELF,0x6,Hi,Too long\r\n\
             SCRIPT,1,0x0,a,b\r\n",
        )
        .unwrap();

        let report = patch_translated_elf(&original, &translated, &csv, None).unwrap();
        assert_eq!(report.rows_processed, 2);
        assert_eq!(report.rows_patched, 0);
        assert_eq!(report.skipped_other, 1);
        assert_eq!(report.skipped_too_large, 1);
        assert_eq!(fs::read(&translated).unwrap(), b"HEADERHiTAIL");
    }
}
