use std::{collections::BTreeMap, fs, path::Path};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

use crate::{
    compress_lz77, datafat::parse_entries_from_data, decompress_lz77, find_row, read_entries,
    size_field_write_offset, slot_capacity,
};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TexturePatchReport {
    pub files_processed: usize,
    pub patches_applied: usize,
    pub streams_written: usize,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextureInjectReport {
    pub streams_injected: usize,
    pub bytes_written: usize,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct TexturePatchEntry {
    file_id: u32,
    tim2_index: usize,
    picture_index: usize,
    png: String,
    mode: Option<String>,
    lz77_offset: Option<usize>,
}

#[derive(Debug, Clone)]
struct Tim2Picture {
    index: usize,
    picture_offset: usize,
    image_size: u32,
    clut_size: u32,
    header_size: usize,
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

pub fn patch_textures_from_manifest(
    data_bin: impl AsRef<Path>,
    manifest_path: impl AsRef<Path>,
    out_dir: impl AsRef<Path>,
) -> Result<TexturePatchReport> {
    let data_bin = data_bin.as_ref();
    let manifest_path = manifest_path.as_ref();
    let out_dir = out_dir.as_ref();
    let manifest = fs::read_to_string(manifest_path)
        .with_context(|| format!("failed to read {}", manifest_path.display()))?;
    let entries: Vec<TexturePatchEntry> = serde_json::from_str(&manifest)
        .with_context(|| format!("failed to parse {}", manifest_path.display()))?;
    fs::create_dir_all(out_dir)
        .with_context(|| format!("failed to create {}", out_dir.display()))?;

    let rows = read_entries(data_bin)?;
    let mut by_file: BTreeMap<u32, Vec<TexturePatchEntry>> = BTreeMap::new();
    for entry in entries {
        by_file.entry(entry.file_id).or_default().push(entry);
    }

    let mut report = TexturePatchReport {
        files_processed: 0,
        patches_applied: 0,
        streams_written: 0,
        errors: Vec::new(),
    };
    let data =
        fs::read(data_bin).with_context(|| format!("failed to read {}", data_bin.display()))?;

    for (file_id, patches) in by_file {
        report.files_processed += 1;
        let Some(row) = find_row(&rows, file_id) else {
            report.errors.push(format!("ID {file_id} not found"));
            continue;
        };
        let start = row.offset as usize;
        let end = start.saturating_add(row.size as usize).min(data.len());
        if start >= end {
            report
                .errors
                .push(format!("ID {file_id} has invalid FAT range"));
            continue;
        }
        match process_texture_file(&data[start..end], &patches) {
            Ok((stream, applied)) => {
                fs::write(out_dir.join(format!("ID_{file_id:05}.lz77")), stream)?;
                report.patches_applied += applied;
                report.streams_written += 1;
            }
            Err(err) => report.errors.push(format!("ID {file_id}: {err}")),
        }
    }

    Ok(report)
}

pub fn inject_patched_texture_streams(
    data_bin_path: impl AsRef<Path>,
    streams_dir: impl AsRef<Path>,
) -> Result<TextureInjectReport> {
    let data_bin_path = data_bin_path.as_ref();
    let streams_dir = streams_dir.as_ref();
    let mut data = fs::read(data_bin_path)
        .with_context(|| format!("failed to read {}", data_bin_path.display()))?;
    let rows = parse_entries_from_data(&data)?;
    let mut report = TextureInjectReport {
        streams_injected: 0,
        bytes_written: 0,
        errors: Vec::new(),
    };

    if !streams_dir.exists() {
        bail!(
            "patched texture stream dir not found: {}",
            streams_dir.display()
        );
    }

    for entry in fs::read_dir(streams_dir)
        .with_context(|| format!("failed to read {}", streams_dir.display()))?
    {
        let path = entry?.path();
        if path.extension().is_none_or(|ext| ext != "lz77") {
            continue;
        }
        let Some(file_id) = parse_patched_stream_id(&path) else {
            report
                .errors
                .push(format!("invalid stream name: {}", path.display()));
            continue;
        };
        let stream =
            fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?;
        let Some(target) = find_row(&rows, file_id) else {
            report.errors.push(format!("ID {file_id} not found in FAT"));
            continue;
        };
        let Some(capacity) = slot_capacity(&rows, target) else {
            report
                .errors
                .push(format!("could not compute capacity for ID {file_id}"));
            continue;
        };
        if stream.len() > capacity as usize {
            report.errors.push(format!(
                "ID {file_id} does not fit: {} > {capacity}",
                stream.len()
            ));
            continue;
        }
        let start = target.offset as usize;
        let end = start + capacity as usize;
        if end > data.len() {
            report
                .errors
                .push(format!("ID {file_id} slot outside Data.bin"));
            continue;
        }
        data[start..start + stream.len()].copy_from_slice(&stream);
        data[start + stream.len()..end].fill(0);
        let size_offset = size_field_write_offset(target);
        data[size_offset..size_offset + 4].copy_from_slice(&(stream.len() as u32).to_le_bytes());
        report.streams_injected += 1;
        report.bytes_written += stream.len();
    }

    fs::write(data_bin_path, data)
        .with_context(|| format!("failed to write {}", data_bin_path.display()))?;
    Ok(report)
}

fn parse_patched_stream_id(path: &Path) -> Option<u32> {
    path.file_stem()?
        .to_str()?
        .strip_prefix("ID_")?
        .parse()
        .ok()
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

fn process_texture_file(raw: &[u8], patches: &[TexturePatchEntry]) -> Result<(Vec<u8>, usize)> {
    let nested = patches.iter().any(|patch| patch.lz77_offset.is_some());
    if nested {
        if patches.iter().any(|patch| patch.lz77_offset.is_none()) {
            bail!("cannot mix direct and nested texture patches in one file");
        }
        let mut raw = raw.to_vec();
        let mut by_offset: BTreeMap<usize, Vec<&TexturePatchEntry>> = BTreeMap::new();
        for patch in patches {
            by_offset
                .entry(patch.lz77_offset.unwrap_or(0))
                .or_default()
                .push(patch);
        }
        let mut applied = 0;
        for (nested_off, group) in by_offset {
            let old_size = lz77_stream_size(&raw, nested_off)?;
            let mut blob = decompress_lz77(&raw[nested_off..nested_off + old_size], None, false)?;
            for patch in group {
                apply_texture_patch(&mut blob, patch)?;
                applied += 1;
            }
            let new_stream = compress_lz77(&blob, false);
            if decompress_lz77(&new_stream, None, false)? != blob {
                bail!("nested LZ77 roundtrip failed at 0x{nested_off:X}");
            }
            if new_stream.len() > old_size {
                bail!(
                    "nested LZ77 at 0x{nested_off:X} does not fit: {} > {old_size}",
                    new_stream.len()
                );
            }
            raw[nested_off..nested_off + new_stream.len()].copy_from_slice(&new_stream);
            raw[nested_off + new_stream.len()..nested_off + old_size].fill(0);
        }
        return Ok((raw, applied));
    }

    let was_lz77 = raw.starts_with(b"LZ77");
    let mut blob = if was_lz77 {
        decompress_lz77(raw, None, false)?
    } else {
        raw.to_vec()
    };
    for patch in patches {
        apply_texture_patch(&mut blob, patch)?;
    }
    if was_lz77 {
        let stream = compress_lz77(&blob, false);
        if decompress_lz77(&stream, None, false)? != blob {
            bail!("LZ77 roundtrip failed");
        }
        Ok((stream, patches.len()))
    } else {
        Ok((blob, patches.len()))
    }
}

fn apply_texture_patch(blob: &mut [u8], patch: &TexturePatchEntry) -> Result<()> {
    if patch.mode.as_deref().unwrap_or("preserve_palette") != "preserve_palette" {
        bail!("only preserve_palette texture patches are supported");
    }
    let tm2 = find_tim2_files(blob)
        .into_iter()
        .nth(patch.tim2_index)
        .with_context(|| format!("TIM2 {} not found", patch.tim2_index))?;
    let pic = tm2
        .pictures
        .into_iter()
        .find(|pic| pic.index == patch.picture_index)
        .with_context(|| format!("picture {} not found", patch.picture_index))?;
    let (width, height, rgba) = read_png_rgba(Path::new(&patch.png))?;
    if width != pic.width as u32 || height != pic.height as u32 {
        bail!(
            "PNG dimensions {width}x{height} do not match TIM2 {}x{}",
            pic.width,
            pic.height
        );
    }
    let image_data = encode_picture_preserve_palette(&pic, &rgba)?;
    if image_data.len() != pic.image_size as usize {
        bail!(
            "encoded image size {} does not match original {}",
            image_data.len(),
            pic.image_size
        );
    }
    let image_offset = pic.picture_offset + pic.header_size;
    blob[image_offset..image_offset + image_data.len()].copy_from_slice(&image_data);
    Ok(())
}

fn encode_picture_preserve_palette(pic: &Tim2Picture, rgba: &[u8]) -> Result<Vec<u8>> {
    match pic.image_type {
        5 => encode_indexed8_preserve(pic, rgba),
        3 | 4 => encode_indexed4_preserve(pic, rgba),
        other => bail!("image_type {other} is not supported for preserve_palette"),
    }
}

fn encode_indexed8_preserve(pic: &Tim2Picture, rgba: &[u8]) -> Result<Vec<u8>> {
    let palette = decode_clut_rgba32(pic, true);
    let pixels = pic.width as usize * pic.height as usize;
    let mut out = Vec::with_capacity(pixels);
    for i in 0..pixels {
        out.push(closest_index(&rgba[i * 4..i * 4 + 4], &palette) as u8);
    }
    Ok(out)
}

fn encode_indexed4_preserve(pic: &Tim2Picture, rgba: &[u8]) -> Result<Vec<u8>> {
    let mut palette = decode_clut_rgba32(pic, false);
    palette.truncate(
        usize::from(if pic.clut_color_count == 0 {
            16
        } else {
            pic.clut_color_count
        })
        .min(16),
    );
    let pixels = pic.width as usize * pic.height as usize;
    let mut out = Vec::with_capacity((pixels + 1) / 2);
    for i in (0..pixels).step_by(2) {
        let low = closest_index(&rgba[i * 4..i * 4 + 4], &palette) as u8 & 0x0f;
        let high = if i + 1 < pixels {
            (closest_index(&rgba[(i + 1) * 4..(i + 1) * 4 + 4], &palette) as u8 & 0x0f) << 4
        } else {
            0
        };
        out.push(low | high);
    }
    Ok(out)
}

fn closest_index(color: &[u8], palette: &[[u8; 4]]) -> usize {
    let mut best = 0;
    let mut best_distance = i64::MAX;
    for (idx, candidate) in palette.iter().enumerate() {
        let dr = color[0] as i64 - candidate[0] as i64;
        let dg = color[1] as i64 - candidate[1] as i64;
        let db = color[2] as i64 - candidate[2] as i64;
        let da = color[3] as i64 - candidate[3] as i64;
        let distance = dr * dr + dg * dg + db * db + da * da * 4;
        if distance < best_distance {
            best_distance = distance;
            best = idx;
            if distance == 0 {
                break;
            }
        }
    }
    best
}

fn read_png_rgba(path: &Path) -> Result<(u32, u32, Vec<u8>)> {
    let file =
        fs::File::open(path).with_context(|| format!("failed to open {}", path.display()))?;
    let decoder = png::Decoder::new(file);
    let mut reader = decoder.read_info()?;
    let mut buf = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf)?;
    let data = &buf[..info.buffer_size()];
    let rgba = match info.color_type {
        png::ColorType::Rgba => data.to_vec(),
        png::ColorType::Rgb => {
            let mut out = Vec::with_capacity(info.width as usize * info.height as usize * 4);
            for chunk in data.chunks_exact(3) {
                out.extend([chunk[0], chunk[1], chunk[2], 255]);
            }
            out
        }
        png::ColorType::Grayscale => data.iter().flat_map(|v| [*v, *v, *v, 255]).collect(),
        png::ColorType::GrayscaleAlpha => data
            .chunks_exact(2)
            .flat_map(|v| [v[0], v[0], v[0], v[1]])
            .collect(),
        other => bail!("unsupported PNG color type: {other:?}"),
    };
    Ok((info.width, info.height, rgba))
}

fn lz77_stream_size(stream: &[u8], offset: usize) -> Result<usize> {
    if stream.get(offset..offset + 4) != Some(b"LZ77") || offset + 12 > stream.len() {
        bail!("no valid LZ77 stream at 0x{offset:X}");
    }
    let comp_size = u32::from_le_bytes([
        stream[offset + 8],
        stream[offset + 9],
        stream[offset + 10],
        stream[offset + 11],
    ]) as usize;
    let size = 12 + comp_size;
    if comp_size == 0 || offset + size > stream.len() {
        bail!("truncated LZ77 stream at 0x{offset:X}");
    }
    Ok(size)
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
            picture_offset: pos,
            image_size,
            clut_size,
            header_size,
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
