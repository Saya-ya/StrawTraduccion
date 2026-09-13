use std::{fs, path::Path};

use anyhow::{bail, Context, Result};
use serde::Serialize;

use crate::{decompress_lz77, read_entries};

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TextureRecord {
    pub id: u32,
    pub fat_row: usize,
    pub file_offset: u32,
    pub raw_size: u32,
    pub compressed: bool,
    pub nested_lz77_offset: Option<usize>,
    pub nested_lz77_size: Option<usize>,
    pub tim2_index: usize,
    pub tim2_offset: usize,
    pub picture_index: usize,
    pub width: u16,
    pub height: u16,
    pub image_type: u8,
    pub image_type_name: String,
    pub image_size: u32,
    pub clut_size: u32,
    pub clut_color_count: u16,
    pub score_ui: i32,
    pub score_font: i32,
    pub png: String,
}

#[derive(Debug, Clone)]
struct Tim2Picture {
    index: usize,
    image_size: u32,
    clut_size: u32,
    clut_color_count: u16,
    image_type: u8,
    width: u16,
    height: u16,
    image_data: Vec<u8>,
    clut_data: Vec<u8>,
}

#[derive(Debug, Clone)]
struct Tim2File {
    offset: usize,
    pictures: Vec<Tim2Picture>,
}

pub fn write_texture_inventory(
    data_bin: impl AsRef<Path>,
    out_dir: impl AsRef<Path>,
) -> Result<Vec<TextureRecord>> {
    let data_bin = data_bin.as_ref();
    let records = texture_inventory(data_bin)?;
    let out_dir = out_dir.as_ref();
    fs::create_dir_all(out_dir)
        .with_context(|| format!("failed to create {}", out_dir.display()))?;
    let png_dir = out_dir.join("png");
    fs::create_dir_all(&png_dir)
        .with_context(|| format!("failed to create {}", png_dir.display()))?;

    let mut records = records;
    for record in &mut records {
        if let Some(png_path) = write_record_png(data_bin, &png_dir, record)? {
            record.png = format!(
                "png/{}",
                png_path.file_name().unwrap_or_default().to_string_lossy()
            );
        }
    }

    let json_path = out_dir.join("textures.json");
    fs::write(&json_path, serde_json::to_string_pretty(&records)?)
        .with_context(|| format!("failed to write {}", json_path.display()))?;

    let csv_path = out_dir.join("textures.csv");
    let mut writer = csv::Writer::from_path(&csv_path)
        .with_context(|| format!("failed to write {}", csv_path.display()))?;
    for record in &records {
        writer.serialize(record)?;
    }
    writer.flush()?;
    Ok(records)
}

fn texture_inventory(data_bin: impl AsRef<Path>) -> Result<Vec<TextureRecord>> {
    let data_bin = data_bin.as_ref();
    let data =
        fs::read(data_bin).with_context(|| format!("failed to read {}", data_bin.display()))?;
    let rows = read_entries(data_bin)?;
    let mut records = Vec::new();

    for row in rows.into_iter().filter(|row| row.is_file && row.size > 0) {
        let start = row.offset as usize;
        let end = start.saturating_add(row.size as usize).min(data.len());
        if start >= end || end > data.len() {
            continue;
        }
        let raw = &data[start..end];
        let mut candidates = Vec::new();
        if raw.starts_with(b"LZ77") {
            if let Ok(blob) = decompress_lz77(raw, None, false) {
                candidates.push((blob, true, None, None));
            }
        } else {
            candidates.push((raw.to_vec(), false, None, None));
            for (nested_off, nested_size, blob) in iter_nested_lz77(raw) {
                candidates.push((blob, true, Some(nested_off), Some(nested_size)));
            }
        }

        for (blob, compressed, nested_lz77_offset, nested_lz77_size) in candidates {
            for (tim2_index, tim2) in find_tim2_files(&blob).into_iter().enumerate() {
                for pic in tim2.pictures {
                    let unique = unique_index_count(&pic);
                    records.push(TextureRecord {
                        id: row.id,
                        fat_row: row.row,
                        file_offset: row.offset,
                        raw_size: row.size,
                        compressed,
                        nested_lz77_offset,
                        nested_lz77_size,
                        tim2_index,
                        tim2_offset: tim2.offset,
                        picture_index: pic.index,
                        width: pic.width,
                        height: pic.height,
                        image_type: pic.image_type,
                        image_type_name: image_type_name(pic.image_type).to_owned(),
                        image_size: pic.image_size,
                        clut_size: pic.clut_size,
                        clut_color_count: pic.clut_color_count,
                        score_ui: score_ui(&pic, unique),
                        score_font: score_font(&pic, unique),
                        png: String::new(),
                    });
                }
            }
        }
    }

    Ok(records)
}

fn write_record_png(
    data_bin: &Path,
    png_dir: &Path,
    record: &TextureRecord,
) -> Result<Option<std::path::PathBuf>> {
    let data =
        fs::read(data_bin).with_context(|| format!("failed to read {}", data_bin.display()))?;
    let start = record.file_offset as usize;
    let end = start
        .saturating_add(record.raw_size as usize)
        .min(data.len());
    if start >= end {
        return Ok(None);
    }
    let raw = &data[start..end];
    let blob = if let Some(nested_off) = record.nested_lz77_offset {
        let Some(nested_size) = record.nested_lz77_size else {
            return Ok(None);
        };
        if nested_off + nested_size > raw.len() {
            return Ok(None);
        }
        decompress_lz77(&raw[nested_off..nested_off + nested_size], None, false)?
    } else if record.compressed {
        decompress_lz77(raw, None, false)?
    } else {
        raw.to_vec()
    };

    let Some(tm2) = find_tim2_files(&blob).into_iter().nth(record.tim2_index) else {
        return Ok(None);
    };
    let Some(pic) = tm2
        .pictures
        .into_iter()
        .find(|pic| pic.index == record.picture_index)
    else {
        return Ok(None);
    };
    let Ok(rgba) = picture_to_rgba(&pic) else {
        return Ok(None);
    };

    let nested = record
        .nested_lz77_offset
        .map(|offset| format!("_L{offset:06X}"))
        .unwrap_or_default();
    let name = format!(
        "ID_{:05}{}_T{:03}_P{:02}_{}x{}.png",
        record.id, nested, record.tim2_index, record.picture_index, record.width, record.height
    );
    let path = png_dir.join(name);
    write_png_rgba(&path, pic.width as u32, pic.height as u32, &rgba)?;
    Ok(Some(path))
}

fn iter_nested_lz77(raw: &[u8]) -> Vec<(usize, usize, Vec<u8>)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(idx) = find_bytes(&raw[pos..], b"LZ77") {
        let off = pos + idx;
        pos = off + 4;
        if off + 12 > raw.len() {
            continue;
        }
        let comp_size =
            u32::from_le_bytes([raw[off + 8], raw[off + 9], raw[off + 10], raw[off + 11]]) as usize;
        let stream_size = 12 + comp_size;
        if comp_size == 0 || off + stream_size > raw.len() {
            continue;
        }
        if let Ok(blob) = decompress_lz77(&raw[off..off + stream_size], None, false) {
            out.push((off, stream_size, blob));
        }
    }
    out
}

fn find_tim2_files(data: &[u8]) -> Vec<Tim2File> {
    let mut found = Vec::new();
    let mut pos = 0;
    while let Some(idx) = find_bytes(&data[pos..], b"TIM2") {
        let off = pos + idx;
        if let Some(tm2) = parse_tim2(data, off) {
            found.push(tm2);
        }
        pos = off + 4;
    }
    found
}

fn parse_tim2(data: &[u8], offset: usize) -> Option<Tim2File> {
    if data.get(offset..offset + 16)?[0..4] != *b"TIM2" {
        return None;
    }
    let version = data[offset + 4];
    let num_pictures = read_u16(data, offset + 6)? as usize;
    if !(3..=4).contains(&version) || num_pictures == 0 || num_pictures > 256 {
        return None;
    }
    let mut pos = offset + 16;
    let mut pictures = Vec::new();
    for index in 0..num_pictures {
        if pos + 48 > data.len() {
            return None;
        }
        let total_size = read_u32(data, pos)?;
        let clut_size = read_u32(data, pos + 4)?;
        let image_size = read_u32(data, pos + 8)?;
        let header_size = read_u16(data, pos + 0x0c)? as usize;
        let clut_color_count = read_u16(data, pos + 0x0e)?;
        let image_type = *data.get(pos + 0x13)?;
        let width = read_u16(data, pos + 0x14)?;
        let height = read_u16(data, pos + 0x16)?;
        if header_size < 48
            || total_size < header_size as u32
            || total_size != header_size as u32 + image_size + clut_size
        {
            return None;
        }
        let image_start = pos + header_size;
        let image_end = image_start + image_size as usize;
        let clut_end = image_end + clut_size as usize;
        if width == 0 || height == 0 || width > 8192 || height > 8192 || clut_end > data.len() {
            return None;
        }
        pictures.push(Tim2Picture {
            index,
            image_size,
            clut_size,
            clut_color_count,
            image_type,
            width,
            height,
            image_data: data[image_start..image_end].to_vec(),
            clut_data: data[image_end..clut_end].to_vec(),
        });
        pos += total_size as usize;
    }
    Some(Tim2File { offset, pictures })
}

fn picture_to_rgba(pic: &Tim2Picture) -> Result<Vec<u8>> {
    match pic.image_type {
        5 => indexed8_to_rgba(pic),
        3 | 4 => indexed4_to_rgba(pic),
        0 => rgba32_to_rgba(pic),
        1 => rgb24_to_rgba(pic),
        2 => rgba5551_to_rgba_bytes(pic),
        other => bail!("unsupported TIM2 image_type {other}"),
    }
}

fn indexed8_to_rgba(pic: &Tim2Picture) -> Result<Vec<u8>> {
    let palette = decode_clut_rgba32(pic, true);
    let mut out = Vec::with_capacity(pic.width as usize * pic.height as usize * 4);
    for index in pic
        .image_data
        .iter()
        .take(pic.width as usize * pic.height as usize)
    {
        out.extend(
            palette
                .get(*index as usize)
                .copied()
                .unwrap_or([255, 0, 255, 255]),
        );
    }
    pad_rgba(&mut out, pic);
    Ok(out)
}

fn indexed4_to_rgba(pic: &Tim2Picture) -> Result<Vec<u8>> {
    let palette = decode_clut_rgba32(pic, false);
    let mut out = Vec::with_capacity(pic.width as usize * pic.height as usize * 4);
    for byte in &pic.image_data {
        out.extend(
            palette
                .get((byte & 0x0f) as usize)
                .copied()
                .unwrap_or([255, 0, 255, 255]),
        );
        out.extend(
            palette
                .get((byte >> 4) as usize)
                .copied()
                .unwrap_or([255, 0, 255, 255]),
        );
    }
    out.truncate(pic.width as usize * pic.height as usize * 4);
    pad_rgba(&mut out, pic);
    Ok(out)
}

fn rgba32_to_rgba(pic: &Tim2Picture) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(pic.width as usize * pic.height as usize * 4);
    for chunk in pic
        .image_data
        .chunks(4)
        .take(pic.width as usize * pic.height as usize)
    {
        if chunk.len() == 4 {
            out.extend([chunk[0], chunk[1], chunk[2], ps2_alpha_to_png(chunk[3])]);
        } else {
            out.extend([255, 0, 255, 255]);
        }
    }
    pad_rgba(&mut out, pic);
    Ok(out)
}

fn rgb24_to_rgba(pic: &Tim2Picture) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(pic.width as usize * pic.height as usize * 4);
    for chunk in pic
        .image_data
        .chunks(3)
        .take(pic.width as usize * pic.height as usize)
    {
        if chunk.len() == 3 {
            out.extend([chunk[0], chunk[1], chunk[2], 255]);
        } else {
            out.extend([255, 0, 255, 255]);
        }
    }
    pad_rgba(&mut out, pic);
    Ok(out)
}

fn rgba5551_to_rgba_bytes(pic: &Tim2Picture) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(pic.width as usize * pic.height as usize * 4);
    for chunk in pic
        .image_data
        .chunks(2)
        .take(pic.width as usize * pic.height as usize)
    {
        if chunk.len() == 2 {
            let px = u16::from_le_bytes([chunk[0], chunk[1]]);
            let r5 = px & 0x1f;
            let g5 = (px >> 5) & 0x1f;
            let b5 = (px >> 10) & 0x1f;
            let a1 = (px >> 15) & 1;
            out.extend([
                ((r5 << 3) | (r5 >> 2)) as u8,
                ((g5 << 3) | (g5 >> 2)) as u8,
                ((b5 << 3) | (b5 >> 2)) as u8,
                if a1 != 0 { 255 } else { 0 },
            ]);
        } else {
            out.extend([255, 0, 255, 255]);
        }
    }
    pad_rgba(&mut out, pic);
    Ok(out)
}

fn decode_clut_rgba32(pic: &Tim2Picture, unswizzle: bool) -> Vec<[u8; 4]> {
    let default_colors = if matches!(pic.image_type, 3 | 4) {
        16
    } else {
        256
    };
    let colors = usize::from(if pic.clut_color_count == 0 {
        default_colors
    } else {
        pic.clut_color_count
    })
    .min(pic.clut_data.len() / 4);
    let raw = (0..colors)
        .map(|i| {
            let base = i * 4;
            [
                pic.clut_data[base],
                pic.clut_data[base + 1],
                pic.clut_data[base + 2],
                ps2_alpha_to_png(pic.clut_data[base + 3]),
            ]
        })
        .collect::<Vec<_>>();
    if unswizzle && colors >= 256 && pic.image_type == 5 {
        (0..colors)
            .map(|i| {
                raw.get(unswizzle_psmt8_clut_index(i))
                    .copied()
                    .unwrap_or([0, 0, 0, 0])
            })
            .collect()
    } else {
        raw
    }
}

fn pad_rgba(out: &mut Vec<u8>, pic: &Tim2Picture) {
    let expected = pic.width as usize * pic.height as usize * 4;
    while out.len() < expected {
        out.extend([255, 0, 255, 255]);
    }
    out.truncate(expected);
}

fn ps2_alpha_to_png(alpha: u8) -> u8 {
    if alpha == 0 {
        0
    } else {
        alpha.saturating_mul(2)
    }
}

fn unswizzle_psmt8_clut_index(i: usize) -> usize {
    (i & !0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)
}

fn write_png_rgba(path: &Path, width: u32, height: u32, rgba: &[u8]) -> Result<()> {
    if rgba.len() != width as usize * height as usize * 4 {
        bail!("invalid RGBA length for PNG");
    }
    let file =
        fs::File::create(path).with_context(|| format!("failed to create {}", path.display()))?;
    let mut encoder = png::Encoder::new(file, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header()?;
    writer.write_image_data(rgba)?;
    Ok(())
}

fn unique_index_count(pic: &Tim2Picture) -> Option<usize> {
    match pic.image_type {
        5 => Some(
            pic.image_data
                .iter()
                .copied()
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
        ),
        3 | 4 => {
            let mut vals = std::collections::BTreeSet::new();
            for byte in &pic.image_data {
                vals.insert(byte & 0x0f);
                vals.insert((byte >> 4) & 0x0f);
            }
            Some(vals.len())
        }
        _ => None,
    }
}

fn score_ui(pic: &Tim2Picture, unique: Option<usize>) -> i32 {
    let mut score = 0;
    let aspect = if pic.height == 0 {
        0.0
    } else {
        pic.width as f32 / pic.height as f32
    };
    if matches!(pic.image_type, 3 | 4 | 5) {
        score += 6;
    }
    if aspect >= 3.0 && pic.height <= 64 {
        score += 40;
    } else if aspect >= 2.0 && pic.height <= 96 {
        score += 25;
    } else if aspect >= 1.5 && pic.height <= 128 {
        score += 10;
    }
    if matches!(pic.height, 16 | 24 | 32 | 40 | 48 | 56 | 64) {
        score += 12;
    }
    if let Some(unique) = unique {
        if unique <= 4 {
            score += 14;
        } else if unique <= 16 {
            score += 10;
        } else if unique <= 48 {
            score += 3;
        } else {
            score -= 6;
        }
    }
    if pic.width >= 512 && pic.height >= 384 {
        score -= 25;
    }
    score
}

fn score_font(pic: &Tim2Picture, unique: Option<usize>) -> i32 {
    let mut score = 0;
    if matches!(pic.image_type, 3 | 4 | 5) {
        score += 10;
    }
    if pic.width == pic.height && matches!(pic.width, 128 | 256 | 512) {
        score += 30;
    }
    if pic.width % 16 == 0 && pic.height % 16 == 0 {
        score += 8;
    }
    if unique.is_some_and(|count| count <= 20) {
        score += 10;
    }
    score
}

fn image_type_name(image_type: u8) -> &'static str {
    match image_type {
        0 => "PSMCT32/RGBA32?",
        1 => "PSMCT24/RGB24?",
        2 => "PSMCT16/RGB5A1?",
        3 => "INDEX4?",
        4 => "INDEX4",
        5 => "INDEX8",
        _ => "UNKNOWN",
    }
}

fn read_u16(data: &[u8], offset: usize) -> Option<u16> {
    data.get(offset..offset + 2)
        .map(|bytes| u16::from_le_bytes([bytes[0], bytes[1]]))
}

fn read_u32(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4)
        .map(|bytes| u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_minimal_tim2() {
        let mut data = b"TIM2".to_vec();
        data.extend([4, 0]);
        data.extend(1_u16.to_le_bytes());
        data.extend([0; 8]);
        data.extend(52_u32.to_le_bytes());
        data.extend(0_u32.to_le_bytes());
        data.extend(4_u32.to_le_bytes());
        data.extend(48_u16.to_le_bytes());
        data.extend(0_u16.to_le_bytes());
        data.extend([0, 0, 0, 0]);
        data.extend(1_u16.to_le_bytes());
        data.extend(1_u16.to_le_bytes());
        data.extend([0; 24]);
        data.extend([1, 2, 3, 4]);

        let parsed = parse_tim2(&data, 0).unwrap();
        assert_eq!(parsed.pictures.len(), 1);
        assert_eq!(parsed.pictures[0].image_type, 0);
        assert_eq!(parsed.pictures[0].width, 1);
    }
}
