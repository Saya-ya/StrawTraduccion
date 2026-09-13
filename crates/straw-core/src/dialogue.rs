use std::{fs, path::Path};

use anyhow::{Context, Result};
use encoding_rs::SHIFT_JIS;

use crate::find_segment_containing;

const TEXT_BLOCK_SIG: &[u8] = &[
    0x03, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
];
const TEXT_BLOCK_HEADER_BEFORE_SIG: usize = 8;
const TEXT_START_OFFSET: usize = TEXT_BLOCK_HEADER_BEFORE_SIG + TEXT_BLOCK_SIG.len();
const CONTINUATION_MARKER: &[u8] = &[
    0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
];
const SCRIPT_DIALOGUE_MASK: u32 = 0xFFFFFF00;
const SCRIPT_DIALOGUE_SIG: u32 = 0x02000000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportedText {
    pub source: String,
    pub script_id: i64,
    pub byte_offset: i64,
    pub section_id: i64,
    pub section_order: i64,
    pub original_text: String,
    pub original_bytes: i64,
    pub segment_start: i64,
    pub segment_capacity: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AnalyzedScript {
    pub script_id: i64,
    pub script_type: String,
    pub variant: String,
    pub total_sections: i64,
    pub texts: Vec<ImportedText>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TextBlock {
    block_offset: usize,
    text_offset: usize,
    text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PointerEntry {
    pointer: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Section {
    id: i64,
    texts: Vec<TextBlock>,
}

pub fn analyze_script_dec(path: impl AsRef<Path>) -> Result<Option<AnalyzedScript>> {
    let path = path.as_ref();
    let data = fs::read(path).with_context(|| format!("failed to read {}", path.display()))?;
    if data.len() < 0x20 {
        return Ok(None);
    }

    let h0 = read_u32_le(&data, 0).unwrap_or(0);
    if (h0 & SCRIPT_DIALOGUE_MASK) != SCRIPT_DIALOGUE_SIG {
        return Ok(None);
    }

    let script_id = script_id_from_path(path).unwrap_or(0);
    let bytecode_ptr = read_u32_le(&data, 0x10).unwrap_or(0x2010) as usize;
    let gap = data.get(0x20..bytecode_ptr).unwrap_or_default();
    let variant = if gap.iter().any(|byte| *byte != 0) {
        "B"
    } else {
        "A"
    };

    let mut text_blocks = find_text_blocks(&data);
    let mut seen_offsets = text_blocks
        .iter()
        .map(|block| block.text_offset)
        .collect::<Vec<_>>();
    for block in find_text_blocks_fallback(&data) {
        if block.text_offset % 2 == 0 && !seen_offsets.contains(&block.text_offset) {
            seen_offsets.push(block.text_offset);
            text_blocks.push(block);
        }
    }
    text_blocks.sort_by_key(|block| block.text_offset);

    let sections = if variant == "B" {
        group_by_pointers(
            parse_pointer_table(&data, bytecode_ptr),
            text_blocks,
            bytecode_ptr,
        )
    } else {
        group_variant_a(&data, text_blocks)
    };

    let mut texts = Vec::new();
    for section in &sections {
        for (idx, block) in section.texts.iter().enumerate() {
            let segment = find_segment_containing(&data, block.text_offset, &block.text).ok();
            texts.push(ImportedText {
                source: "SCRIPT".to_owned(),
                script_id,
                byte_offset: block.text_offset as i64,
                section_id: section.id,
                section_order: idx as i64 + 1,
                original_text: block.text.clone(),
                original_bytes: block.text.encode_utf16().count() as i64 * 2,
                segment_start: segment
                    .as_ref()
                    .map(|seg| seg.start as i64)
                    .unwrap_or(block.text_offset as i64),
                segment_capacity: segment
                    .as_ref()
                    .map(|seg| seg.capacity_bytes() as i64)
                    .unwrap_or(0),
            });
        }
    }

    Ok(Some(AnalyzedScript {
        script_id,
        script_type: format!("0x{h0:08X}"),
        variant: variant.to_owned(),
        total_sections: sections.len() as i64,
        texts,
    }))
}

pub fn extract_elf_strings(path: impl AsRef<Path>) -> Result<Vec<ImportedText>> {
    let path = path.as_ref();
    let data = fs::read(path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut strings = Vec::new();
    let mut i = 0;
    while i + 1 < data.len() {
        let b1 = data[i];
        if is_sjis_lead(b1) {
            let start = i;
            while i + 1 < data.len() {
                let b = data[i];
                if is_sjis_lead(b) {
                    i += 2;
                } else if (0x20..=0x7e).contains(&b) {
                    i += 1;
                } else {
                    break;
                }
            }
            let raw = &data[start..i];
            if raw.len() >= 4 {
                let (decoded, _, had_errors) = SHIFT_JIS.decode(raw);
                let text = decoded.trim().to_owned();
                if !had_errors && !text.is_empty() && !text.is_ascii() && is_valid_elf_text(&text) {
                    strings.push(ImportedText {
                        source: "ELF".to_owned(),
                        script_id: -1,
                        byte_offset: start as i64,
                        section_id: 0,
                        section_order: strings.len() as i64 + 1,
                        original_text: text,
                        original_bytes: raw.len() as i64,
                        segment_start: start as i64,
                        segment_capacity: raw.len() as i64,
                    });
                }
            }
            continue;
        }
        i += 1;
    }
    Ok(strings)
}

fn find_text_blocks(data: &[u8]) -> Vec<TextBlock> {
    let mut blocks = Vec::new();
    let mut seen = Vec::new();
    let mut pos = 0;
    while let Some(idx) = find_bytes(&data[pos..], TEXT_BLOCK_SIG) {
        let sig_pos = pos + idx;
        let Some(block_start) = sig_pos.checked_sub(TEXT_BLOCK_HEADER_BEFORE_SIG) else {
            pos = sig_pos + 1;
            continue;
        };
        let text_start = block_start + TEXT_START_OFFSET;
        if text_start >= data.len() || seen.contains(&text_start) {
            pos = sig_pos + 1;
            continue;
        }
        let Some(null_pos) = find_utf16_null(data, text_start) else {
            pos = sig_pos + 1;
            continue;
        };
        if null_pos == text_start {
            pos = sig_pos + 1;
            continue;
        }
        if let Ok(text) = decode_utf16le(&data[text_start..null_pos]) {
            seen.push(text_start);
            blocks.push(TextBlock {
                block_offset: block_start,
                text_offset: text_start,
                text,
            });
        }
        pos = null_pos + 2;
    }
    blocks
}

fn parse_pointer_table(data: &[u8], bytecode_ptr: usize) -> Vec<PointerEntry> {
    let mut entries = Vec::new();
    for offset in (0x20..bytecode_ptr.min(data.len())).step_by(16) {
        if offset + 16 > data.len() {
            break;
        }
        let ptr = read_u32_le(data, offset).unwrap_or(0) as usize;
        let count = read_u32_le(data, offset + 4).unwrap_or(0);
        if ptr != 0 || count != 0 {
            entries.push(PointerEntry { pointer: ptr });
        }
    }
    entries
}

fn group_by_pointers(
    entries: Vec<PointerEntry>,
    text_blocks: Vec<TextBlock>,
    bytecode_ptr: usize,
) -> Vec<Section> {
    let mut boundaries = vec![bytecode_ptr];
    for entry in entries {
        if !boundaries.contains(&entry.pointer) {
            boundaries.push(entry.pointer);
        }
    }
    boundaries.sort_unstable();
    let mut sections = Vec::new();
    for idx in 0..boundaries.len() {
        let start = boundaries[idx];
        let end = boundaries.get(idx + 1).copied().unwrap_or(usize::MAX);
        let texts = text_blocks
            .iter()
            .filter(|block| block.block_offset >= start && block.block_offset < end)
            .cloned()
            .collect::<Vec<_>>();
        if !texts.is_empty() {
            sections.push(Section {
                id: sections.len() as i64,
                texts,
            });
        }
    }
    sections
}

fn group_variant_a(data: &[u8], text_blocks: Vec<TextBlock>) -> Vec<Section> {
    let mut sections: Vec<Section> = Vec::new();
    for block in text_blocks {
        let continuation = block.block_offset >= 16
            && data.get(block.block_offset - 16..block.block_offset) == Some(CONTINUATION_MARKER);
        if sections.is_empty() || !continuation {
            sections.push(Section {
                id: sections.len() as i64,
                texts: Vec::new(),
            });
        }
        if let Some(section) = sections.last_mut() {
            section.texts.push(block);
        }
    }
    sections
}

fn find_text_blocks_fallback(data: &[u8]) -> Vec<TextBlock> {
    let mut blocks = Vec::new();
    for parity in [0, 1] {
        let mut i = parity;
        while i + 4 < data.len() {
            let word = u16::from_le_bytes([data[i], data[i + 1]]);
            if is_jp_codepoint(word) {
                let start = i;
                let mut end = i + 2;
                while end + 1 < data.len() {
                    let w = u16::from_le_bytes([data[end], data[end + 1]]);
                    if w == 0 {
                        break;
                    }
                    if is_jp_codepoint(w) || (0x20..=0x7e).contains(&w) || w == 0x0a || w == 0x0d {
                        end += 2;
                    } else {
                        break;
                    }
                }
                if let Ok(text) = decode_utf16le(&data[start..end]) {
                    let text = text.trim_matches(['\r', '\n']).to_owned();
                    if is_valid_jp(&text) {
                        blocks.push(TextBlock {
                            block_offset: start,
                            text_offset: start,
                            text,
                        });
                    }
                }
                i = end;
                continue;
            }
            i += 2;
        }
    }
    blocks
}

fn is_valid_jp(text: &str) -> bool {
    let len = text.chars().count();
    if len < 4 {
        return false;
    }
    let hiragana = text
        .chars()
        .filter(|&ch| ('\u{3040}'..='\u{309F}').contains(&ch))
        .count();
    let katakana = text
        .chars()
        .filter(|&ch| ('\u{30A0}'..='\u{30FF}').contains(&ch))
        .count();
    let kanji = text
        .chars()
        .filter(|&ch| ('\u{4E00}'..='\u{9FFF}').contains(&ch))
        .count();
    let total = hiragana + katakana + kanji;
    if total == 0 || total * 2 < len || (hiragana == 0 && kanji > 0) {
        return false;
    }
    let particles = [
        'の', 'は', 'が', 'に', 'を', 'て', 'で', 'と', 'か', 'な', 'だ', 'し', 'い', 'う', 'る',
        '？', '！', '、', '。', '「', '」', '…',
    ];
    (len <= 8 && katakana + kanji == len) || text.chars().any(|ch| particles.contains(&ch))
}

fn is_valid_elf_text(text: &str) -> bool {
    let len = text.chars().count();
    let jp = text.chars().filter(|&ch| is_jp_char(ch)).count();
    if jp < 4 || jp * 2 < len {
        return false;
    }
    let mut max_cons = 0;
    let mut cur = 0;
    for ch in text.chars() {
        if is_jp_char(ch) {
            cur += 1;
            max_cons = max_cons.max(cur);
        } else {
            cur = 0;
        }
    }
    max_cons >= 4
        && text.chars().any(|ch| {
            ('\u{3040}'..='\u{309F}').contains(&ch) || ('\u{30A0}'..='\u{30FF}').contains(&ch)
        })
}

fn is_jp_codepoint(cp: u16) -> bool {
    (0x3040..=0x309f).contains(&cp)
        || (0x30a0..=0x30ff).contains(&cp)
        || (0x4e00..=0x9fff).contains(&cp)
        || (0x3000..=0x303f).contains(&cp)
        || (0xff00..=0xffef).contains(&cp)
        || (0x2000..=0x206f).contains(&cp)
}

fn is_jp_char(ch: char) -> bool {
    ('\u{3040}'..='\u{309F}').contains(&ch)
        || ('\u{30A0}'..='\u{30FF}').contains(&ch)
        || ('\u{4E00}'..='\u{9FFF}').contains(&ch)
        || ('\u{3000}'..='\u{303F}').contains(&ch)
        || ('\u{FF00}'..='\u{FFEF}').contains(&ch)
        || ('\u{2000}'..='\u{206F}').contains(&ch)
}

fn is_sjis_lead(byte: u8) -> bool {
    (0x81..=0x9f).contains(&byte) || (0xe0..=0xef).contains(&byte)
}

fn decode_utf16le(data: &[u8]) -> Result<String> {
    let units = data
        .chunks_exact(2)
        .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
        .collect::<Vec<_>>();
    String::from_utf16(&units).context("invalid UTF-16LE text")
}

fn find_utf16_null(data: &[u8], start: usize) -> Option<usize> {
    let mut pos = start;
    while pos + 1 < data.len() {
        if data[pos..pos + 2] == [0, 0] {
            return Some(pos);
        }
        pos += 2;
    }
    None
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn read_u32_le(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4)
        .map(|bytes| u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

fn script_id_from_path(path: &Path) -> Option<i64> {
    path.file_stem()?
        .to_str()?
        .strip_prefix("ID_")?
        .parse()
        .ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_opcode_text_block() {
        let mut data = vec![0; 0x30];
        data[0..4].copy_from_slice(&SCRIPT_DIALOGUE_SIG.to_le_bytes());
        data[0x10..0x14].copy_from_slice(&0x20_u32.to_le_bytes());
        let block_start = 0x30;
        data.extend([0; TEXT_BLOCK_HEADER_BEFORE_SIG]);
        data.extend(TEXT_BLOCK_SIG);
        data.extend("こんにちは".encode_utf16().flat_map(u16::to_le_bytes));
        data.extend([0, 0, 0, 0]);

        let blocks = find_text_blocks(&data);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].block_offset, block_start);
        assert_eq!(blocks[0].text, "こんにちは");
    }
}
