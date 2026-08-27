# StrawTraduccion — Tutorial (Step by Step)

> A guided walkthrough for translating *Strawberry Panic!* (PS2) from start to finish.  
> Follow each part in order the first time. After that, you can jump to any section.

---

## Part 1 — What This Tool Does and Why

Strawberry Panic! is a PS2 visual novel. All its text lives inside two files on the game disc: `Data.bin` (the dialogue scripts, compressed with a PS2-specific LZSS algorithm and encoded in UTF-16LE) and `SLPS_256.11` (the ELF executable, with menus and system text in Shift-JIS).

StrawTraduccion is a suite of Python tools that:

1. **Extracts** every text string from both files.
2. **Loads** them into a database you can browse and edit through a web browser.
3. **Rebuilds** the game files with your translations injected, producing a playable patched ISO.

The web interface is the centerpiece. It runs on your machine at `http://127.0.0.1:8080` and gives you an inline editor, search, progress tracking, and a one-click build button.

### What you need

- Your own ISO of the game (the project includes **no** copyrighted files).
- Python 3.10 or newer.
- About 5 minutes to set up.

---

## Part 2 — Installation and First Launch

### 2.1 Clone and install dependencies

```bash
git clone https://github.com/Saya-ya/StrawTraduccion.git
cd StrawTraduccion
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
pip install fastapi uvicorn sqlalchemy jinja2 python-multipart
```

There is no `requirements.txt` — the dependency list is intentionally short. Every library is either part of Python's standard library or one of these five packages.

### 2.2 Place your game files

Extract `Data.bin` and `SLPS_256.11` from your ISO (any tool like 7-Zip or PowerISO works) and drop them into `originales/`:

```
originales/
├── Data.bin          # ~1.2 GB, contains 27,411 internal files
└── SLPS_256.11       # ~4 MB, the PS2 executable
```

Without these two files, nothing works. The extraction tools read them directly; the build tools copy them and patch the copies.

### 2.3 Start the server

```bash
python run_webapp.py
```

You should see Uvicorn booting on `http://127.0.0.1:8080`. Open that URL. The page will redirect you to `/scripts`, which is empty for now — that is expected.

---

## Part 3 — Importing the Game Text

Go to the **Import** tab in the navbar. This is the extraction & database population page.

### What happens when you click "Extract and Import"

The button triggers a chain of four operations, one after the other:

1. **`extract_all.py`** — `Data.bin` has an internal file allocation table (FAT) with 27,411 entries. 997 of those files are LZSS-compressed. The script decompresses each one and saves it to `work/scripts_extraidos/ID_XXXXX.dec`. This step takes the longest. If the `.dec` files already exist it skips entirely, which is useful on re-runs.

2. **`dialogue_order.py`** — scans every `.dec` file looking for text strings. It identifies which 58 scripts contain dialogue (marked with `0x03` opcode) and detects narrative sections and branching paths. Outputs `textos/dialogo.csv` (the master CSV) and `work/dialogue_order.json` (metadata).

3. **`extract_dialogue.py --elf`** — reads `SLPS_256.11` and pulls out Shift-JIS strings: menu labels, character names, system messages. Appends them to the same CSV with `source=ELF` and `file_id=ELF`.

4. **`import_service.py`** — reads the final CSV and writes everything into `work/translation_manager.db` (SQLite). It also computes **byte capacities** for every text entry by reading the `.dec` files — more on this later.

> **Heads up:** the import locks the database. If a build is running, the import will refuse to start and return a 409 error. This protects the data from corruption.

### After import

The Scripts page now shows entries. The top bar displays global totals: how many scripts, how many text entries total, and your overall translation progress (0% at this point).

---

## Part 4 — Understanding the Scripts Page

The Scripts page is where you will spend 90% of your time. Let's break it into its layers.

### 4.1 The script list

You see a table of scripts. Each row tells you:

- **Script ID** — the internal file number from Data.bin's FAT.
- **Type / variant** — technical metadata. "SCRIPT_DIALOGUE" means a playable dialogue scene. "UNKNOWN" means a compressed file that is not a scene (UI data, effect tables, etc.).
- **Supported** — the green badge means the script was auto-detected as dialogue. Only **58 scripts** get this badge. The other ~939 are marked "unsupported" — they contain game data that needs manual hex editing, not the web tool.
- **Progress bar** — `translated_texts / total_texts` with a pink fill.
- **⚠ shift** — a count of entries in this script whose translations do not fit in the available byte space. You will learn to deal with this soon.

Use the filter at the top ("Supported only") to hide unsupported scripts and focus on what matters.

### 4.2 Inside a script — sections and context

Click any script ID to open the detail view. The first thing you notice is a row of colored buttons at the top: **sections**.

Dialogue scripts are not flat. The game engine uses sections to handle branching: different routes through the story, conditional dialogue, scene transitions. Each section is a self-contained sequence of text lines displayed in order.

- **Pink** = the section you are viewing right now.
- **Green ✓** = that section is 100% translated.
- **Red ⚠** = that section has at least one entry marked "needs shift" (overflow).

Click a section to switch to it. Your active filter (All / Untranslated / Translated / Needs shift) is preserved across section changes.

### 4.3 The text entry cards

Each card is one line of game text. Reading top to bottom, you see:

**Context lines** — the two entries before and the two after appear in gray above and below the current entry. This is so you can read the surrounding dialogue without leaving the page. It is assembled by looking up `section_order ± 1` and `section_order ± 2` in the same section.

**Metadata header** — `[section:order] 0x{BYTE_OFFSET} (#{entry_id})`. The byte offset is the exact position inside the `.dec` file where this string lives.

**Original text** — the Japanese, shown in gray.

**Translated text** — your translation, shown in white on a semi-dark background. If empty, a gray italic hint says "Click to translate."

**Fit indicator** — a colored circle on the right side:
- 🟢 **OK** — at least 20 bytes of slack remain after your translation.
- 🟡 **Tight** — fits, but fewer than 20 bytes free. Patches fine, just borderline.
- 🔴 **Needs shift** — does NOT fit. The build will **preserve the original Japanese** for this entry. You must shorten your translation.
- ⚪ **Unchecked** — no translation saved yet.

This fit indicator is **live**. Every time you save a translation, the system re-encodes it through the glyph map (if active), measures the byte count, and compares it against the capacity. If you edit later and the new version fits, the icon updates.

---

## Part 5 — Translating: The Inline Editor

Click any text entry card. It transforms into an editor **without reloading the page** (HTMX swaps the card's HTML).

### What you see

- A **textarea** that auto-sizes between 3 and 20 rows depending on content length.
- The original Japanese text above the textarea for reference.
- A real-time **byte counter** that updates as you type, showing: `used / capacity (+remaining free)`.

### How the byte counter works

The counter runs in JavaScript on every keystroke. It multiplies `textarea.value.length * 2` (UTF-16LE uses 2 bytes per character) and adds 2 for the null terminator. This is a simplification, but it is close enough for real-time feedback — the actual encoding happens server-side on save.

The color changes based on slack:
- **Green** = 20+ bytes free (comfortable).
- **Yellow** = 0–19 bytes free (tight but fine).
- **Red** = exceeded (will be flagged `needs_shift`).

> **Important:** If you have a custom glyph map active (e.g. Spanish mode), the actual encoded bytes may differ from `length * 2` because accented characters map to Cyrillic glyphs that take the same 2 bytes each. The server-side fit check is authoritative; the JS counter is a guide.

### Saving — `Ctrl+Enter`

Press `Ctrl+Enter` or click the pink Save button. Here is the full sequence that runs:

1. **Line break auto-insertion** — the system checks if the original Japanese has `\r\n` breaks and your translation does not. If so, it calculates where breaks should go using proportional mapping. More on this below.
2. **Glyph-aware encoding** — if the target language is Spanish (or Custom), the text is run through the glyph map first (á→Г, é→Д, etc.), then encoded to UTF-16LE (scripts) or Shift-JIS (ELF).
3. **Fit check** — the encoded byte count is compared against `segment_capacity`.
4. **Database update** — `translated_text`, `is_translated`, `fit_status`, `needs_shift`, and `updated_at` are written. The lock is released.
5. **Script counter** — `scripts.translated_texts` is recalculated by counting all translated entries in that script.

### Canceling — `Escape`

Press `Escape` or click Cancel. The card returns to read-only mode. The lock is released.

### The locking system

When you open the editor, the entry is locked: `locked_by = "user"`, `locked_at = now()`. If another person opens the same entry within 60 seconds, they see a yellow message: "Locked by user." After 60 seconds the lock expires automatically so stale locks do not block work.

This is designed for teams working on the same server. If you are the only translator, you will never notice it.

---

## Part 6 — The Line Break System Explained

The game uses `\r\n` (carriage return + line feed, 4 bytes in UTF-16LE) to split dialogue text across multiple lines inside a text box. The original Japanese has these breaks placed carefully so lines have balanced visual width.

When you translate, you usually type a continuous string without worrying about breaks. The system handles them for you.

### How it works, step by step

1. **Proportional extraction** — the original Japanese is scanned. Every `\r\n` position is converted to a proportion: `position / total_length`. If the original is 60 characters and a break is at char 30, the proportion is 0.5.

2. **Mapping to the translation** — each proportion is multiplied by your translation's length to get a target insertion point.

3. **Smart placement** — the system searches up to 12 characters forward and backward from the target point, looking for a natural break location:
   - Punctuation: `.`, `,`, `;`, `:`, `!`, `?`, `…`, `—`, `)`, `@`
   - Uppercase letters at sentence starts (preceded by space or period)
   - It takes the closest valid spot in each direction

4. **Insertion** — `\r\n` is placed **after** the separator character found.

5. **Cleanup** — trailing breaks are stripped; consecutive breaks beyond 2 are collapsed.

### What this means in practice

You write:
```
I wanted to see her again. But I knew that wasn't possible today.
```

If the original had a break after the equivalent of the period after "again", the system outputs:
```
I wanted to see her again.\r\nBut I knew that wasn't possible today.
```

The player sees two lines in the text box:
```
I wanted to see her again.
But I knew that wasn't possible today.
```

> **Caveat:** If no good break point is found within the 12-character window, that break is **skipped** entirely. The system prefers fewer breaks over mid-word breaks. Your whole translation still displays — just with fewer line splits than the original.

> **Caveat 2:** Break insertion happens **before** the fit check. If adding breaks pushes the byte count over capacity, the entry gets flagged `needs_shift`. You can either shorten the text or manually remove/reposition breaks.

---

## Part 7 — The Glyph Mapping System

This is the most unusual part of the project. Here is the problem and the solution.

### The problem

The game's font is Japanese. It contains hiragana, katakana, kanji, ASCII, and — crucially — the Cyrillic alphabet. It does **not** contain á, é, í, ó, ú, ñ, ¡, ¿, or any other non-ASCII Latin characters.

You need accented characters to write proper Spanish. The font simply does not have them.

### The solution: two layers

**Layer 1 — Character swap (build time):** When rebuilding game files, every accented character is silently replaced with a Cyrillic character that looks nothing like it but occupies a slot in the font. A Glyph that **does** render.

**Layer 2 — Texture replacement (runtime, PCSX2):** PCSX2 has a texture dumping/injection system. You provide a PNG that replaces the Cyrillic glyph's texture with the correct accented character. The game engine draws Cyrillic `Г`; the emulator intercepts and draws `á` instead.

### The Spanish map

| You type | Game stores | PCSX2 draws |
|---|---|---|
| á | Г (U+0413) | á |
| é | Д (U+0414) | é |
| í | Е (U+0415) | í |
| ó | Ж (U+0416) | ó |
| ú | З (U+0417) | ú |
| ñ | И (U+0418) | ñ |
| Ñ | Й (U+0419) | Ñ |
| ¡ | К (U+041A) | ¡ |
| ¿ | Л (U+041B) | ¿ |

Uppercase Á/É/Í/Ó/Ú/Ü map to the same glyphs as their lowercase counterparts. The pre-made texture PNG in `Replacement/` covers the Spanish set.

### English mode

Set target language to "English" in Settings. The glyph map becomes empty — all text passes through as-is. English uses only ASCII, which the Japanese font supports natively. No texture replacements needed.

### Custom mode (other languages)

Set target language to "Custom." A table of **64 available Cyrillic glyphs** appears. For each glyph you want to use, type the Latin-1 character it should represent. Polish example:

| Glyph | Map to |
|---|---|
| Г | ą |
| Д | ć |
| Е | ę |
| Ж | ł |

Only Latin-1 characters are accepted (the validation rejects anything else). The map is saved as JSON in the database and applied during all future builds and fit checks.

You must create your own PCSX2 texture replacements matching your assignments. The naming convention is determined by PCSX2's texture hashing; the `Replacement/` folder shows the format.

---

## Part 8 — Capacity, Slack, and "Needs Shift"

### Where capacity comes from

Inside a `.dec` file, each text string looks like this in memory:

```
[UTF-16LE characters] [00 00 null terminator] [00 00 00 00 ... zero padding] [next data structure]
```

The capacity for a text entry is:
```
capacity = slack_end - text_start
```
That is: the distance from the first byte of the string to the first **non-zero** byte after all the zero padding. The padding is dead space — originally put there by the developers, probably from alignment or leftover editor buffers.

During import, `compute_capacity()` scans the `.dec` file for each entry and stores the value in `text_entries.segment_capacity`.

### What "needs shift" means

"Shift" is short for "this translation needs to shift the data structures after it to make room" — which the tool **cannot currently do safely**. The rebuild strategy is conservative, called **local-slack mode**:

- It reads the `.dec` file, finds each text segment by the byte offset from the CSV.
- It overwrites the UTF-16LE content and the null terminator.
- It counts available zero-padding after the terminator.
- If the new encoded text fits in `content + terminator + padding`, it writes it and fills the rest with zeros.
- If it does NOT fit (exceeds the padding and would overwrite the next data structure), the entry is **skipped** — the original Japanese stays in place.

This approach **never moves pointers or restructures data**. It is foolproof — the game will not crash — but it means translations must be concise.

Typical capacities per entry are **150–230 bytes** (75–115 characters in UTF-16LE, less if `\r\n` breaks are present).

### How to handle overflow

When you see a 🔴 "needs shift" entry:

1. **Shorten the translation.** Remove filler words, rephrase. Spanish is often more verbose than Japanese; you sometimes need to compress.
2. **Remove a line break.** Each `\r\n` costs 4 bytes. If the original has 3 breaks and you made 3 breaks, try using 2 — the system will still auto-insert proportionally, but having fewer target breaks reduces byte pressure.
3. **Accept the skip.** Some entries simply cannot fit in the slot. The original text stays — your translation is still saved in the database for reference, just not patched into this particular build.

---

## Part 9 — Search: Finding Needles in the Haystack

The **Search** tab searches across all scripts simultaneously, powered by SQLite FTS5.

### Basic search

Type any word and hit Enter. The dropdown lets you restrict to **Japanese only** (search `original_text`), **Translation only** (search `translated_text`), or **Both** (default).

### Position reference syntax

You can mix search terms with exact position filters:

| Syntax | Meaning |
|---|---|
| `[3:15]` | Section 3, order 15 |
| `0x04A2C` | Byte offset 0x04A2C |
| `(#4582)` | Entry ID 4582 |

Combine them: `告白 [1:5]` finds "告白" entries in section 1 order 5. `[3:]` with no order filters by section only.

### From search result to editor

Search results show a table. The **Script ID** column is a link. Click it to jump directly to the script detail view, scrolled to that entry, with the editor auto-opened. This is the fastest way to navigate to a specific line you remember.

If the result is in a script with many sections, the system calculates which page the entry lives on and redirects you there.

---

## Part 10 — Building the ISO

When you have enough translations, go to **Build**.

### Export CSV (optional)

Two buttons let you download the database as CSV:
- **Export all** — every entry, translated or not.
- **Export translated only** — only entries with `is_translated = true`.

This is useful for backups, sharing with other translators, or running the CLI tools manually.

### The build pipeline

Click **"Build ISO"** and the page shows a live progress log. The build runs in a separate process (`build_worker.py`) so the web server stays responsive.

**Phase 1: Export (5%)**
All translated entries are written to a CSV in `work/build_temp/`.

**Phase 2: Prepare Data.bin (8%)**
A fresh copy of `originales/Data.bin` is made as `work/Data_patched.bin`. The build always starts from a clean copy — never patching an already-patched file.

**Phase 3: Compress scripts (10–25%)**
This is the heavy lifting. Up to 8 worker processes run in parallel, each one:
1. Decompresses the `.dec` file (LZSS decompression).
2. Rebuilds the decompressed data with translations injected (local-slack mode, glyph-mapped).
3. Re-compresses with LZSS.
4. **Verifies round-trip:** decompresses the fresh compression and compares byte-for-byte with the rebuilt data. If they do not match exactly, the script is rejected. This catches any compression bugs before they reach the ISO.

Scripts with `needs_shift` entries still get processed — the overflow entries are simply skipped (original text preserved).

**Phase 4: Inject into Data.bin (50%)**
Each compressed script is written into its FAT slot in `Data_patched.bin`. The FAT entry's size field is updated. Because of the FAT quirk (see below), the tool reads the size from a specific offset.

**Phase 5: Apply ELF translations (70%)**
`apply_translation.py` reads the CSV and patches Shift-JIS strings in `SLPS_256.11`. This covers menus and system text.

**Phase 6: Generate ISO (80%)**
`build_iso.py` replaces the `Data.bin` inside the original ISO with the patched version.

**Phase 7: Inject ELF (95%)**
`inject_elf.py` replaces the `SLPS_256.11` inside the ISO with the patched version.

**Output:** `work/Strawberry_translated.iso`

### Important build details

- **Only one build at a time.** A PID lock file (`work/build.lock`) enforces this. If the worker crashes, the lock is detected as stale.
- **Builds do NOT modify your database.** All translations are read-only; the build reads from the DB and writes to disk.
- **The original ISO is never touched.** The pipeline copies, patches, and writes a new file.

---

## Part 11 — Delegation: Working as a Team

The **Delegation** tab solves a common fan translation workflow: one person coordinates, multiple people translate different scripts offline.

### Exporting work packages

1. Select a range of scripts from the list.
2. Check **"Only untranslated"** if you want to assign fresh work (skips entries that already have translations).
3. Enter a label (e.g., "EquipoA_Lunes") — this becomes part of the filename.
4. Click **"Export"**. You get a CSV file compatible with the main `dialogo.csv` format.

### Importing completed work

A translator sends back the CSV with the `translated_text` column filled in. Upload it:

1. Each row is matched by `(script_id, byte_offset, original_text)` — all three must match.
2. If the entry in the database already has a **different** translation, it counts as a **conflict** (the upload is ignored for that row).
3. If the entry is empty or has the same translation, it is imported.
4. The response tells you how many were imported, how many conflicted, and how many were skipped.

> **Tip:** Export, translate, import in small batches (5–10 scripts at a time). This reduces the chance of two people accidentally translating the same entries.

---

## Part 12 — Settings

Access via the ⚙ icon in the top-right corner.

### UI Language

Changes the language of the web interface (menus, buttons, messages). Two options: Spanish (ES) or English (EN). Stored in a cookie and in the `settings` table. Changing it reloads the page.

### Target Language

Controls which glyph map is used when encoding translations. Three options:

- **Spanish**: applies the hardcoded 16-entry Spanish map. This is the default.
- **English**: applies an empty map (ASCII passthrough). Use this for English translations.
- **Custom**: applies a user-defined map from the glyph configuration table below.

### Glyph Configuration

Only visible/editable when target language is "Custom." Shows all 64 available Cyrillic glyphs with a text input next to each. Type one Latin-1 character per glyph you want to map. Leave blank for glyphs you do not use. Click **"Save Map"** — it validates that every input is a valid Latin-1 character and saves as JSON.

> **Switching languages after translating:** If you wrote translations in Spanish mode and switch to English mode, the fit check will re-evaluate with the new (empty) glyph map. The byte count will be different — possibly tighter or looser — because accented characters are no longer being substituted. The database translations themselves do not change, only the encoding used during build/fit-check.

---

## Part 13 — PCSX2 Setup (Texture Replacement)

After building the ISO, you need the emulator side working:

1. In PCSX2, go to **Settings → Graphics → Texture Replacement** and enable "Load Textures."
2. Locate PCSX2's texture folder — typically `textures/SLPS-25611/` inside your PCSX2 directory.
3. Copy the PNG file from `Replacement/` into that folder.
4. **Boot the game fresh** (File → Boot ISO). Do NOT load a savestate — savestates bypass the texture loading path.
5. If the Spanish accented characters display correctly, the replacement is working.

---

## Part 14 — Technical Notes (Things That Will Bite You)

### The FAT quirk

`Data.bin`'s internal file allocation table has 27,411 entries of 12 bytes each. A non-obvious detail:

> The `size_field` in row **i** stores the size of the file in row **i - 1** (the previous row). The actual size of file **i** is in row **i + 1**.

`datafat.py` handles this correctly. The build pipeline uses it. If you ever manually edit FAT entries, remember this offset.

### The LZSS header is 12 bytes, not 16

Standard LZSS uses a 16-byte header. The PS2 game uses 12 bytes. If you feed a 16-byte header to the game's decompressor, the output is off by 6,905 bytes — and the game shows a black screen. The custom `lz77.py` implementation uses 12 bytes throughout.

### `@` markers must stay in place

The at-sign `@` (and fullwidth `＠`) is a **click-to-advance** marker. The game engine pauses text rendering when it hits one. If you move or remove these in your translation, the dialogue pacing breaks. Copy them at the same positions as the original.

### Savestates are your enemy during testing

PCSX2 savestates capture the entire emulator state, including **already-loaded textures**. If you add new texture replacements, a savestate will still show the old ones. Always cold-boot when testing glyph replacements.

### The web server is localhost-only

`run_webapp.py` binds to `127.0.0.1:8080`. It is not designed for network access — no authentication, no HTTPS, no rate limiting. Only run it locally.

### 58 supported scripts, 939 not

The tool automates dialogue translation for 58 detected scripts. The remaining ~939 compressed files in Data.bin are non-dialogue data (UI layouts, effect parameters, lookup tables). Translating those requires opening the `.dec` files in a hex editor and manually identifying and editing strings — the web interface does not help with them.

---

## Part 15 — Typical Workflow Summary

A full session from scratch:

```
1.  Place Data.bin and SLPS_256.11 in originales/
2.  python run_webapp.py
3.  Open http://127.0.0.1:8080
4.  Go to Import → Extract and Import (wait ~2–5 min)
5.  Go to Scripts → filter "Supported only"
6.  Pick a script → pick a section → filter "Untranslated"
7.  Click entry → type translation → Ctrl+Enter to save
8.  Watch the fit indicator: keep translations in 🟢 or 🟡 zone
9.  Repeat 6–8 for every script/section
10. Check Dashboard occasionally to see overall progress
11. Search tab to fix specific entries or find untranslated text
12. When done (or to test): go to Build → Build ISO
13. Copy Replacement/*.png to PCSX2 textures folder
14. Cold-boot the ISO in PCSX2 and test
```
