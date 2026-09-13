pub mod datafat;
pub mod extract;
pub mod fit_checker;
pub mod glyph_map;
pub mod lz77;
pub mod script_rebuilder;

pub use datafat::{
    find_row, parse_entries, read_entries, size_field_write_offset, slot_capacity, FatEntry,
};
pub use extract::{extract_lz77_scripts, write_extracted_scripts, ExtractedScript};

pub use fit_checker::{check_fit, FitResult, FitStatus, TextSource};
pub use glyph_map::{
    encode_game_sjis, encode_game_utf16, english_glyph_map, game_string, invert_glyph_map,
    normalize_glyph_map, spanish_glyph_map, GlyphMap,
};
pub use lz77::{compress_lz77, decompress_lz77};
pub use script_rebuilder::{
    find_segment_containing, rebuild_local_slack, RebuildReport, SegmentReport, TextSegment,
    TranslationRow,
};
