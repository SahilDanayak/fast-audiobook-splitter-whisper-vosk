# fast-audiobook-splitter-whisper-vosk

**Automatically split audiobooks and podcasts into chapters by keyword — fast.**

**Why was this tool created?**

Unlike other tools that transcribe the entire 10-hour file (taking hours), this tool only transcribes 10-second snippets around detected silences, finishing in minutes.

**How does it work?**

Hybrid pipeline: FFmpeg silence detection → Vosk offline keyword spotting → OpenAI Whisper verification. Lossless splitting, no re-encoding. ~3–5 minutes for a 10-hour audiobook.

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-orange.svg)](https://ffmpeg.org/)
[![Vosk](https://img.shields.io/badge/Vosk-offline%20ASR-green.svg)](https://alphacephei.com/vosk/)
[![Whisper](https://img.shields.io/badge/OpenAI%20Whisper-verification-lightgrey.svg)](https://github.com/openai/whisper)

---

## What It Does

Splits long MP3, M4B, M4A, FLAC, or OGG audio files into individual chapter files using a three-stage hybrid approach:

1. **FFmpeg silence detection** — scans the full file in 1–2 min to find candidate boundaries
2. **Vosk keyword spotting** — offline ASR checks each candidate for phrases like "Chapter One" in ~2–3 min
3. **Whisper verification** — OpenAI Whisper confirms ambiguous clips for high accuracy

Output: sequentially named chapter files (`chapter_1.mp3`, `chapter_2.mp3`, ...) in a dedicated folder.

**Accuracy:** 95–98% chapter detection  
**Speed:** ~3–5 min for a 10-hour audiobook (4 parallel workers)  
**Formats:** MP3, M4B, M4A, FLAC, OGG  
**Platforms:** Windows, macOS, Linux

---

## Quick Start

### Prerequisites

- Python 3.9+ (3.10/3.11 recommended)
- FFmpeg: `brew install ffmpeg` / `apt install ffmpeg` / [ffmpeg.org](https://ffmpeg.org)

### Install

```bash
git clone https://github.com/SahilDanayak/fast-audiobook-splitter-whisper-vosk.git
cd fast-audiobook-splitter-whisper-vosk

python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

### Download Vosk Model (optional but recommended)

```bash
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
mv vosk-model-small-en-us-0.15 model
```

### Run

```bash
python fast_splitter.py --input "audiobook.mp3"
```

Chapters saved to: `audiobook - Chapters/`

---

## Usage Examples

```bash
# Default: hybrid mode (Vosk + Whisper tiny)
python fast_splitter.py -i "book.mp3"

# Silence detection only — fastest, least accurate
python fast_splitter.py -i "book.mp3" --no-use-vosk --no-use-whisper-verify

# Vosk only — fast, offline, no GPU needed
python fast_splitter.py -i "book.mp3" --no-use-whisper-verify

# Whisper only — slowest, highest accuracy
python fast_splitter.py -i "book.mp3" --no-use-vosk

# Custom keyword (default: "chapter")
python fast_splitter.py -i "book.mp3" --phrase "part"
python fast_splitter.py -i "book.mp3" --phrase "section"
python fast_splitter.py -i "book.mp3" --phrase "act"

# Higher accuracy Whisper model
python fast_splitter.py -i "book.mp3" --whisper-model "base"

# Custom output folder
python fast_splitter.py -i "book.mp3" -o "/path/to/output"

# Test on first 10 minutes before full run
python fast_splitter.py -i "book.mp3" --max-duration 600

# Increase parallelism for faster processing
python fast_splitter.py -i "book.mp3" --max-workers 8

# Tune silence thresholds — more chapters
python fast_splitter.py -i "book.mp3" --noise "-40dB" --silence-len 0.5 --min-chapter-len 120

# Tune silence thresholds — fewer chapters
python fast_splitter.py -i "book.mp3" --noise "-25dB" --silence-len 1.5 --min-chapter-len 300
```

---

## CLI Reference

| Option | Default | Description |
|--------|---------|-------------|
| `-i, --input` | *required* | Input audio file (MP3, M4B, M4A, FLAC, OGG) |
| `-o, --outdir` | input folder | Output directory for chapter files |
| `--phrase` | `chapter` | Keyword to detect (e.g. "part", "section", "act") |
| `--noise` | `-35dB` | FFmpeg silence threshold (lower = more sensitive) |
| `--silence-len` | `3.0s` | Minimum silence duration to register as boundary |
| `--min-chapter-len` | `30s` | Minimum chapter length in seconds |
| `--max-chapter-len` | `1800s` | Maximum chapter length; auto-splits if exceeded |
| `--context-before` | `3.0s` | Audio before silence sent to ASR for verification |
| `--context-after` | `7.0s` | Audio after silence sent to ASR for verification |
| `--use-vosk` / `--no-use-vosk` | enabled | Toggle Vosk keyword spotting |
| `--use-whisper-verify` / `--no-use-whisper-verify` | enabled | Toggle Whisper verification |
| `--whisper-model` | `tiny` | Whisper model size: `tiny` `base` `small` `medium` `large` |
| `--vosk-model-path` | `model` | Path to downloaded Vosk model directory |
| `--max-workers` | `4` | Parallel threads for verification and splitting |
| `--max-duration` | — | Process only first N seconds (for testing) |

---

## Performance

| Mode | 10-hour audiobook | Accuracy |
|------|-------------------|----------|
| Silence detection only | 1–2 min | ~90% |
| Vosk only | 2–3 min | ~95% |
| **Hybrid: Vosk + Whisper tiny** | **3–5 min** | **~99%** |
| Whisper tiny only | 4–6 min | ~99% |
| Whisper base only | 8–15 min | ~99% |

*Benchmarked with 4 parallel workers on Windows.*

---

## How It Works

```
Input audio
    │
    ▼
[1] FFmpeg silencedetect        ← full file scan, ~1–2 min
    │  50–150 candidate boundaries
    ▼
[2] Vosk keyword spotting       ← 10s clips, offline, parallel
    │  verified boundaries only
    ▼
[3] Whisper verification        ← ambiguous clips only, parallel
    │  confirmed chapter starts
    ▼
[4] FFmpeg -c copy split        ← lossless, no re-encoding, parallel
    │
    ▼
chapter_1.mp3, chapter_2.mp3, ...
```

**Why this order?**  
Silence detection is near-free computationally. Vosk is fast and offline. Whisper is accurate but slow — by running it only on ambiguous clips (typically 5–15% of candidates), total processing time drops 10–30× compared to Whisper-only approaches.

---

## Troubleshooting

**Too many chapters detected**
```bash
python fast_splitter.py -i "book.mp3" --noise "-25dB" --silence-len 1.5 --min-chapter-len 300
```

**Too few chapters detected**
```bash
python fast_splitter.py -i "book.mp3" --noise "-40dB" --silence-len 0.5 --min-chapter-len 60
```

**Chapters don't align with content**  
Try alternate keywords: `--phrase "part"`, `--phrase "section"`, `--phrase "act"`

**Processing is slow**
```bash
# Skip Whisper
python fast_splitter.py -i "book.mp3" --no-use-whisper-verify

# More parallelism
python fast_splitter.py -i "book.mp3" --max-workers 8
```

**ffmpeg not found**
```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Linux/Debian
choco install ffmpeg         # Windows
```

**Vosk model missing**
```bash
pip install vosk
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model
```
---

## Dependencies

- [ffmpeg](https://ffmpeg.org/) — audio processing and lossless splitting
- [vosk](https://github.com/alphacep/vosk-api) — offline speech recognition for keyword spotting
- [openai-whisper](https://github.com/openai/whisper) — high-accuracy ASR for boundary verification
- [tqdm](https://github.com/tqdm/tqdm) — progress bars

---

## License

MIT — free to use, modify, and distribute.

---

## Tags

`audiobook` `podcast` `chapter-splitter` `audio-splitter` `ffmpeg` `vosk` `whisper` `openai-whisper` `speech-recognition` `asr` `keyword-spotting` `python` `audio-processing` `m4b` `mp3` `lossless` `offline-asr` `chapter-detection` `long-audio` `automation`