use std::{
    collections::BTreeMap,
    fs,
    io::{Seek, SeekFrom, Write},
    path::Path,
};

use anyhow::{bail, Context, Result};

use crate::{
    datafat::{find_row, parse_entries_from_data, size_field_write_offset, slot_capacity},
    glyph_map::GlyphMap,
    lz77::{compress_lz77, decompress_lz77},
    script_rebuilder::{rebuild_local_slack, TranslationRow},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PatchScriptsReport {
    pub scripts_total: usize,
    pub scripts_patched: usize,
    pub rows_total: usize,
    pub rows_applied: usize,
    pub errors: Vec<String>,
}

pub fn patch_translated_scripts(
    data_bin_path: impl AsRef<Path>,
    dec_dir: impl AsRef<Path>,
    csv_path: impl AsRef<Path>,
    glyph_map: Option<&GlyphMap>,
) -> Result<PatchScriptsReport> {
    let data_bin_path = data_bin_path.as_ref();
    let dec_dir = dec_dir.as_ref();
    let csv_path = csv_path.as_ref();
    let rows_by_script = load_translation_rows(csv_path)?;
    let mut data = fs::read(data_bin_path)
        .with_context(|| format!("failed to read {}", data_bin_path.display()))?;
    let fat_rows = parse_entries_from_data(&data)?;

    let mut report = PatchScriptsReport {
        scripts_total: rows_by_script.len(),
        scripts_patched: 0,
        rows_total: rows_by_script.values().map(Vec::len).sum(),
        rows_applied: 0,
        errors: Vec::new(),
    };

    for (script_id, rows) in rows_by_script {
        match patch_one_script(&mut data, &fat_rows, dec_dir, script_id, &rows, glyph_map) {
            Ok(rows_applied) => {
                report.scripts_patched += 1;
                report.rows_applied += rows_applied;
            }
            Err(err) => report.errors.push(format!("ID {script_id}: {err}")),
        }
    }

    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(data_bin_path)
        .with_context(|| format!("failed to open {}", data_bin_path.display()))?;
    file.seek(SeekFrom::Start(0))?;
    file.write_all(&data)?;
    file.flush()?;

    Ok(report)
}

fn patch_one_script(
    data: &mut [u8],
    fat_rows: &[crate::datafat::FatEntry],
    dec_dir: &Path,
    script_id: i32,
    rows: &[TranslationRow],
    glyph_map: Option<&GlyphMap>,
) -> Result<usize> {
    if script_id < 0 {
        bail!("ELF rows are not script rows");
    }
    let file_id = script_id as u32;
    let dec_path = dec_dir.join(format!("ID_{file_id:05}.dec"));
    let dec_data =
        fs::read(&dec_path).with_context(|| format!("failed to read {}", dec_path.display()))?;
    let (rebuilt, rebuild_report) = rebuild_local_slack(&dec_data, rows, true, glyph_map)?;
    if !rebuild_report.needs_shift.is_empty() {
        bail!("{} segment(s) need shift", rebuild_report.needs_shift.len());
    }

    let compressed = compress_lz77(&rebuilt, false);
    let redecompressed = decompress_lz77(&compressed, None, true)?;
    if redecompressed != rebuilt {
        bail!("compressed roundtrip mismatch");
    }

    let target = find_row(fat_rows, file_id).with_context(|| "file ID not found in FAT")?;
    let capacity =
        slot_capacity(fat_rows, target).with_context(|| "could not compute slot capacity")?;
    if compressed.len() > capacity as usize {
        bail!(
            "compressed stream does not fit ({} > {})",
            compressed.len(),
            capacity
        );
    }

    let start = target.offset as usize;
    let end = start + capacity as usize;
    if end > data.len() {
        bail!("target slot outside Data.bin");
    }
    data[start..start + compressed.len()].copy_from_slice(&compressed);
    data[start + compressed.len()..end].fill(0);

    let size_offset = size_field_write_offset(target);
    data[size_offset..size_offset + 4].copy_from_slice(&(compressed.len() as u32).to_le_bytes());

    Ok(rebuild_report.rows_applied)
}

fn load_translation_rows(csv_path: &Path) -> Result<BTreeMap<i32, Vec<TranslationRow>>> {
    let mut reader = csv::Reader::from_path(csv_path)
        .with_context(|| format!("failed to read {}", csv_path.display()))?;
    let mut rows_by_script: BTreeMap<i32, Vec<TranslationRow>> = BTreeMap::new();

    for (idx, row) in reader.records().enumerate() {
        let row = row?;
        let source = row.get(0).unwrap_or("SCRIPT").to_owned();
        if source != "SCRIPT" {
            continue;
        }
        let file_id = row.get(1).unwrap_or_default();
        let Ok(script_id) = file_id.parse::<i32>() else {
            continue;
        };
        let translated_text = row.get(4).unwrap_or_default().trim().to_owned();
        if translated_text.is_empty() {
            continue;
        }
        let offset = parse_offset(row.get(2).unwrap_or("0"))?;
        rows_by_script
            .entry(script_id)
            .or_default()
            .push(TranslationRow {
                source,
                file_id: script_id,
                offset,
                original_text: row.get(3).unwrap_or_default().to_owned(),
                translated_text,
                csv_line: idx + 2,
            });
    }

    Ok(rows_by_script)
}

fn parse_offset(value: &str) -> Result<usize> {
    let value = value.trim();
    if let Some(hex) = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
    {
        Ok(usize::from_str_radix(hex, 16)?)
    } else {
        Ok(value.parse()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_translation_rows_from_csv() {
        let temp = tempfile::tempdir().unwrap();
        let csv_path = temp.path().join("dialogo.csv");
        fs::write(
            &csv_path,
            "source,file_id,offset,original_text,translated_text\r\nSCRIPT,7,0x10,a,b\r\nELF,ELF,0x20,c,d\r\n",
        )
        .unwrap();

        let rows = load_translation_rows(&csv_path).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[&7][0].offset, 0x10);
        assert_eq!(rows[&7][0].translated_text, "b");
    }
}
