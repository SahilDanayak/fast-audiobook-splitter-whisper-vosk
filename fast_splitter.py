#!/usr/bin/env python3
"""
fast_splitter.py

Fast chapter splitter optimized for speed:
- Runs an ffmpeg silence-detection pass to find candidate boundaries (very fast)
- Optionally runs Vosk keyword-spotting on small windows around candidates
- Optionally verifies candidates using Whisper on tiny clips
- Splits the original file with ffmpeg (copy mode where possible)

Tested on Windows. Also compatible with macOS and Linux. Requires `ffmpeg`.

Usage example:
  python fast_splitter.py -i "longbook.mp3" -o "Chapters" --use_vosk --use_whisper_verify --whisper_model tiny

"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import json
import warnings
from io import StringIO
from contextlib import redirect_stderr
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# Suppress warnings
warnings.filterwarnings("ignore")

# optional imports
try:
    from vosk import Model as VoskModel, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
except Exception:
    VoskModel = None

try:
    import whisper
except Exception:
    whisper = None


def run_silence_detect(input_path: str, noise: str = "-35dB", 
                       duration: float = 0.6, max_duration: int = None) -> list:
    """Run ffmpeg silencedetect and return list of silence (start,end) tuples (seconds).
    We parse silence_end entries as candidate chapter starts.
    """
    cmd = [
        "ffmpeg", "-nostats", "-i", input_path,
        "-af", f"silencedetect=noise={noise}:d={duration}",
        "-f", "null", "-"
    ]
    if max_duration is not None:
        cmd.insert(2, "-t")
        cmd.insert(3, str(max_duration))
    
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    stderr = proc.communicate()[1]
    starts = []
    ends = []
    for line in stderr.splitlines():
        m = re.search(r"silence_start: ([0-9.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", line)
        if m:
            ends.append(float(m.group(1)))
    # pair ends and starts if needed; we'll use ends as candidate boundaries
    return ends


def build_candidates(silence_ends: list, duration: float, min_chapter_len: float = 60.0, max_chapter_len: float = 3600.0) -> list:
    """From silence end times, derive candidate chapter start times.
    Enforce min/max chapter lengths and always include 0.
    """
    candidates = [0.0]
    for t in silence_ends:
        if t <= 2.0:
            continue
        # ensure spacing from last candidate
        if t - candidates[-1] >= min_chapter_len:
            candidates.append(t)
    # enforce max chapter length: insert extra cuts
    i = 0
    while i < len(candidates):
        start = candidates[i]
        nxt = candidates[i+1] if i+1 < len(candidates) else duration
        if nxt - start > max_chapter_len:
            # insert extra cut at start+max_chapter_len
            candidates.insert(i+1, start + max_chapter_len)
        else:
            i += 1
    # ensure final boundary at end (not included as start)
    return candidates


def get_audio_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    out = subprocess.check_output(cmd, text=True).strip()
    try:
        return float(out)
    except Exception:
        return 0.0


def extract_clip(src: str, start: float, length: float, out_path: str, sr: int = 16000):
    cmd = ["ffmpeg", "-y", "-ss", str(max(0, start)), "-t", str(length), "-i", src, "-ar", str(sr), "-ac", "1", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def verify_single_candidate(candidate_data):
    """Verify a single candidate and return (candidate, accepted, debug_info) tuple."""
    c, src, context_before, context_after, use_vosk, use_whisper_verify, vosk_model, whisper_model, phrase, whisper_lock = candidate_data
    
    # extract small clip around c
    clip_start = max(0, c - context_before)
    clip_dur = context_before + context_after
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        clip_path = tmp.name
    extract_clip(src, clip_start, clip_dur, clip_path)

    accepted = False
    vosk_text = ""
    whisper_text = ""
    
    if use_vosk and vosk_model is not None:
        try:
            # better option if not using whisper
            # grammar = f'["{phrase.lower()}", "[unk]"]'
            # rec = KaldiRecognizer(vosk_model, 16000, grammar)
            
            rec = KaldiRecognizer(vosk_model, 16000)
            rec.SetWords(True)
            with open(clip_path, "rb") as f:
                data = f.read()
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                vosk_text = res.get("text", "")
            else:
                res = json.loads(rec.FinalResult())
                vosk_text = res.get("text", "")
            
            if phrase.lower() in vosk_text.lower():
                accepted = True
        except Exception:
            vosk_text = "ERROR"
    
    if not accepted and use_whisper_verify and whisper_model is not None:
        try:
            # Use lock to serialize Whisper access (not thread-safe)
            with whisper_lock:
                with redirect_stderr(StringIO()):
                    res = whisper_model.transcribe(clip_path, language="en", verbose=False)
                whisper_text = res.get("text", "")
            if phrase.lower() in whisper_text.lower():
                accepted = True
        except Exception:
            whisper_text = "ERROR"

    # cleanup clip
    try:
        os.remove(clip_path)
    except Exception:
        pass
    
    return c, accepted, (vosk_text[:50], whisper_text[:50])


def split_with_ffmpeg_copy(src: str, boundaries: list, out_dir: str, max_workers: int = 4) -> list:
    os.makedirs(out_dir, exist_ok=True)
    
    def split_chapter(args):
        i, start, end, src, out_dir = args
        out_name = os.path.join(out_dir, f"chapter_{i+1}.mp3")
        cmd = ["ffmpeg", "-y",  "-ss", str(start), "-i", src]
        if end is not None:
            cmd += ["-to", str(end)]
        cmd += ["-c", "copy", out_name]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return out_name
    
    # Prepare tasks
    tasks = []
    for i, start in enumerate(boundaries):
        end = boundaries[i+1] if i+1 < len(boundaries) else None
        tasks.append((i, start, end, src, out_dir))
    
    # Run in parallel
    parts = []
    max_workers = min(max_workers, len(tasks))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with tqdm(total=len(tasks), desc="Splitting audio", unit="part") as pbar:
            for out_name in executor.map(split_chapter, tasks):
                parts.append(out_name)
                pbar.update(1)
    
    return sorted(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--outdir", default=None, help="Output directory")
    parser.add_argument("--noise", default="-35dB")
    parser.add_argument("--silence-len", type=float, default=3.0)
    parser.add_argument("--min-chapter-len", type=float, default=30.0)
    parser.add_argument("--max-chapter-len", type=float, default=1800.0)
    parser.add_argument("--context-before", type=float, default=3.0)
    parser.add_argument("--context-after", type=float, default=7.0)
    
    # These automatically create --no-use-vosk and --no-use-whisper-verify
    parser.add_argument("--use-vosk", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--vosk-model-path", default="model")
    parser.add_argument("--use-whisper-verify", action=argparse.BooleanOptionalAction, default=True)
    
    parser.add_argument("--whisper-model", default="tiny", choices=["tiny","base","small","medium","large"])
    parser.add_argument("--phrase", default="chapter")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-duration", type=int, default=None, help="Max duration to process (seconds)")
    args = parser.parse_args()

    src = args.input
    # If outdir not specified, create a subfolder based on input filename
    if args.outdir is None:
        input_dir = os.path.dirname(os.path.abspath(src))
        input_name = os.path.splitext(os.path.basename(src))[0]  # Remove extension
        outdir = os.path.join(input_dir, f"{input_name} - Chapters")
    else:
        outdir = args.outdir
    if not os.path.isfile(src):
        print("Input not found", src)
        sys.exit(1)

    # Get audio duration
    duration = get_audio_duration(src)
    if args.max_duration is not None:
        duration = min(duration, args.max_duration)
    duration_mins = duration / 60
    duration_hrs = duration / 3600

    # Startup screen
    mode = []
    if args.use_vosk:
        mode.append("Vosk")
    if args.use_whisper_verify:
        mode.append("Whisper")
    mode_str = " + ".join(mode) if mode else "Silence-only"
    
    # Calculate estimated time for silence detection (10 sec per hour)
    estimated_silence_time = duration_hrs * 10
    
    print(f"\n{'='*80}")
    print(f"Fast Audiobook Splitter")
    print(f"{'='*80}")
    print(f"Input:              {src}")
    if args.max_duration is not None:
        print(f"Duration:           {duration_hrs:.2f} hrs ({duration_mins:.1f} min) [TRIMMED to {args.max_duration}s]")
    else:
        print(f"Duration:           {duration_hrs:.2f} hrs ({duration_mins:.1f} min)")
    print(f"Output:             {outdir}")
    print(f"Silence threshold:  {args.noise}")
    print(f"Silence duration:   {args.silence_len}s")
    print(f"Min chapter:        {args.min_chapter_len}s ({args.min_chapter_len/60:.1f} min)")
    print(f"Max chapter:        {args.max_chapter_len}s ({args.max_chapter_len/60:.1f} min)")
    print(f"Context window:     {args.context_before}s before + {args.context_after}s after")
    if args.use_whisper_verify:
        print(f"Whisper model:      {args.whisper_model}")
    print(f"Chapter phrase:     '{args.phrase}'")
    if args.use_vosk:
        print(f"Vosk model path:    {args.vosk_model_path}")
    print(f"Max workers:        {args.max_workers}")
    print(f"{'='*80}")
    print(f"Mode:               {mode_str}")
    print(f"{'='*80}\n")

    print(f"\nStep 1: Running fast silence-detect pass... (Est. ~{estimated_silence_time:.0f}s)")
    with tqdm(total=1, desc="Silence detection", unit="pass") as pbar:
        silence_ends = run_silence_detect(src, noise=args.noise, duration=args.silence_len, max_duration=args.max_duration)
        pbar.update(1)
    candidates = build_candidates(silence_ends, duration, args.min_chapter_len, args.max_chapter_len)
    print(f"Found {len(candidates)} initial candidates\n")

    # Load Vosk
    vosk_model = None
    if args.use_vosk and VoskModel:
        if os.path.exists(args.vosk_model_path):
            print(f"Loading Vosk model from: {args.vosk_model_path}")
            vosk_model = VoskModel(args.vosk_model_path)
        else:
            print("Vosk model path not found. Disabling Vosk.")
            args.use_vosk = False

    # Load Whisper
    whisper_model = None
    if args.use_whisper_verify and whisper:
        print(f"Loading Whisper model: {args.whisper_model}")
        whisper_model = whisper.load_model(args.whisper_model)

    # Verify candidates by checking small clips around each start (parallelized)
    final_starts = [candidates[0]]
    
    # Create a lock for thread-safe Whisper access
    whisper_lock = Lock()
    
    # Prepare candidate verification tasks
    candidate_tasks = [
        (c, src, args.context_before, args.context_after, args.use_vosk, args.use_whisper_verify, vosk_model, whisper_model, args.phrase, whisper_lock)
        for c in candidates[1:]
    ]
    
    # Use ThreadPoolExecutor for parallel verification
    max_workers = min(args.max_workers, len(candidate_tasks)) if candidate_tasks else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with tqdm(total=len(candidate_tasks), desc="Step 2: Verifying candidates", unit="cand") as pbar:
            try:
                debug_samples = []  # Store a few samples for analysis
                for c, accepted, (vosk_text, whisper_text) in executor.map(verify_single_candidate, candidate_tasks):
                    if accepted:
                        final_starts.append(c)
                    debug_samples.append((c, accepted, vosk_text, whisper_text))
                    pbar.update(1)
                
                # Print debug info
                print(f"\n📊 Chapter samples:")
                for c, accepted, vosk_text, whisper_text in debug_samples:
                    time_str = f"{int(c//60):02d}:{int(c%60):02d}"  # Convert to MM:SS format
                    if not accepted:
                        print(f"  ❌ Rejected | [{time_str}] Vosk: '{vosk_text}' | Whisper: '{whisper_text}'")
                    else:
                        print(f"  ✓ Accepted | [{time_str}] Vosk: '{vosk_text}' | Whisper: '{whisper_text}'")                       
            except Exception as e:
                print(f"\n❌ Error during verification: {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)

    # ensure sorted unique
    final_starts = sorted(list(dict.fromkeys([round(x, 3) for x in final_starts])))
    print(f"\n✓ Accepted {len(final_starts)} chapter starts")

    # final split
    print("\nStep 3: Splitting original file into chapters...")
    parts = split_with_ffmpeg_copy(src, final_starts, outdir, args.max_workers)
    print(f"\n✓ Wrote {len(parts)} files to {outdir}")


if __name__ == '__main__':
    main()
