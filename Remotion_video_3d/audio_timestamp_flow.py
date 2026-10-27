#!/usr/bin/env python3
"""
gen_flow_manifest.py  --  Voiceover -> Google Flow scene plan -> Remotion manifest

Instead of using YOUR scene breaks, this script lets the AUDIO decide the scene breaks:
  * every scene is between MIN and MAX seconds (default 5-8s)
  * every scene ENDS ON A FULL SENTENCE (short sentences are merged into one scene)
  * scenes are pulled toward TARGET seconds (default 6s)
  * only if a full-stop cut is impossible within 5-8s does it cut at a comma, then mid-sentence,
    and it warns you which scenes so you can add a full stop to the script
  * each scene is tagged with the Flow clip length to generate (6s or 8s)
  * output manifest is compatible with the existing FullEpisode.tsx + build_episode_3d.sh

Usage:
  python3 gen_flow_manifest.py <asset_dir> [output.json] [options]

  <asset_dir> must contain:  one .mp3 (voiceover)           -> required
                             one .txt (script)              -> optional (cleaner scene text)
                             Flow clips  NN_*.mp4           -> optional (phase 2)

Options:
  --min 5 --max 8 --target 6     scene length limits / preferred length (seconds)
  --tolerance 0                  optional: let a scene run e.g. 0.5s outside 5-8s rather than cut
                                 mid-sentence (default 0 = strict 5-8s)
  --clips 6,8                    clip lengths Flow can generate
  --model base                   faster-whisper model (base | small | medium)
"""
import os
import re
import sys
import json
import math
import glob
import shutil
import argparse
import difflib
import subprocess

FPS = 30
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(PROJECT_ROOT, "public")
PLAN_PATH = os.path.join(PROJECT_ROOT, "flow_plan.txt")
CACHE_PATH = os.path.join(PROJECT_ROOT, "whisper_words_cache.json")

# ---- Segmentation tuning (cost = lower is better) ----
# Scenes should END ON A FULL SENTENCE. Cutting anywhere else is a last resort and
# is priced so high that it only happens when no sentence-end solution exists in 5-8s.
COMMA_CUT_PENALTY = 40.0     # cut at a comma / colon / dash (last resort 1)
MID_CUT_PENALTY = 120.0      # cut in the middle of a clause   (last resort 2)
PAUSE_BONUS_PER_SEC = 2.0    # among equal options, prefer cutting where the narrator pauses
PAUSE_BONUS_CAP = 0.6        # ...up to this many seconds of pause
TOLERANCE_PENALTY = 25.0     # per second outside min/max (only used when --tolerance > 0)
SHORT_LAST_PENALTY = 5.0     # per second the final scene is under MIN (allowed, but discouraged)
TAIL_PAD_SEC = 0.3           # padding after the last spoken word


def snap(t):
    """Snap a time to the nearest video frame so durations are whole frames (no drift)."""
    return round(t * FPS) / FPS


def clean_word(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


# ----------------------------------------------------------------------------
# 1. Asset ingest
# ----------------------------------------------------------------------------
def prepare_assets(source_dir):
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    mp3s = sorted(glob.glob(os.path.join(source_dir, "*.mp3")))
    if not mp3s:
        print(f"❌ No .mp3 voiceover found in {source_dir}")
        sys.exit(1)
    audio_path = os.path.join(PUBLIC_DIR, "voice_over.mp3")
    if os.path.abspath(mp3s[0]) != os.path.abspath(audio_path):
        shutil.copy2(mp3s[0], audio_path)
    print(f"✅ Voiceover: {os.path.basename(mp3s[0])} -> public/voice_over.mp3")

    script_path = None
    txts = sorted(glob.glob(os.path.join(source_dir, "*.txt")))
    if txts:
        script_path = os.path.join(PROJECT_ROOT, "scenes.txt")
        shutil.copy2(txts[0], script_path)
        print(f"✅ Script: {os.path.basename(txts[0])} -> scenes.txt (used only for clean scene text)")
    else:
        print("ℹ️  No .txt script found: scene text will come from the Whisper transcript.")

    copied = 0
    for f in sorted(glob.glob(os.path.join(source_dir, "*.mp4"))):
        m = re.match(r"^(\d+)[_\-\. ]", os.path.basename(f))
        if not m:
            continue
        dest = os.path.join(PUBLIC_DIR, f"Scene{int(m.group(1)):02d}.mp4")
        if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(f):
            shutil.copy2(f, dest)
        copied += 1
    if copied:
        print(f"✅ Flow clips: {copied} file(s) placed in public/ as SceneNN.mp4")
    else:
        print("ℹ️  No scene clips yet (phase 1). The plan will tell you what to generate.")

    return audio_path, script_path


def probe_duration(path):
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return float(out.stdout.strip())
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 2. Whisper word timestamps (cached, so re-runs in phase 2 are instant)
# ----------------------------------------------------------------------------
def get_words(audio_path, model_name):
    key = f"{os.path.getsize(audio_path)}-{int(os.path.getmtime(audio_path))}-{model_name}"
    if os.path.exists(CACHE_PATH):
        try:
            cached = json.load(open(CACHE_PATH, encoding="utf-8"))
            if cached.get("key") == key:
                print(f"⚡ Using cached Whisper transcript ({len(cached['words'])} words)")
                return cached["words"]
        except Exception:
            pass

    from faster_whisper import WhisperModel  # imported here so the splitter can be tested without it
    print(f"Loading faster-whisper '{model_name}' (CPU, int8)...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    print("Transcribing with word timestamps (this can take a few minutes on a Pi)...")
    segments, _ = model.transcribe(audio_path, beam_size=5, word_timestamps=True, language="en")

    words = []
    for seg in segments:
        for w in seg.words:
            if clean_word(w.word):
                words.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end)})

    json.dump({"key": key, "words": words}, open(CACHE_PATH, "w", encoding="utf-8"))
    return words


# ----------------------------------------------------------------------------
# 3. The splitter: dynamic programming over word boundaries
# ----------------------------------------------------------------------------
def cut_kind(words, j, end_flags):
    """What kind of boundary is between word j-1 and word j: 'sentence' | 'comma' | 'mid'."""
    if end_flags[j - 1]:
        return "sentence"
    if re.search(r"[,;:\u2014\u2013-]$", words[j - 1]["text"]):
        return "comma"
    return "mid"


def _boundary_cost(words, j, end_flags):
    """Cost of cutting between word j-1 and word j."""
    base = {"sentence": 0.0, "comma": COMMA_CUT_PENALTY, "mid": MID_CUT_PENALTY}[cut_kind(words, j, end_flags)]
    gap = max(0.0, words[j]["start"] - words[j - 1]["end"])
    return base - min(gap, PAUSE_BONUS_CAP) * PAUSE_BONUS_PER_SEC


def split_scenes(words, min_len, max_len, target, end_flags, tolerance=0.0):
    """
    Returns a list of (first_word_idx, last_word_idx_exclusive, start_sec, end_sec).
    Scenes are contiguous, frame-aligned, and END ON A FULL SENTENCE whenever that is
    possible within [min_len, max_len]. Short sentences are merged into one scene.
    Only the last scene may be shorter than min_len (if the audio ends that way).
    """
    n = len(words)
    bounds = [0.0] * (n + 1)
    for j in range(1, n):
        bounds[j] = snap((words[j - 1]["end"] + words[j]["start"]) / 2.0)
    bounds[n] = snap(words[-1]["end"] + TAIL_PAD_SEC)

    def run(lo, hi):
        INF = float("inf")
        dp = [INF] * (n + 1)
        back = [-1] * (n + 1)
        dp[0] = 0.0
        for j in range(1, n + 1):
            for i in range(j - 1, -1, -1):
                d = bounds[j] - bounds[i]
                if d > hi + tolerance + 1e-6:
                    break
                if dp[i] == INF:
                    continue
                cost = (d - target) ** 2
                if j < n:
                    cost += _boundary_cost(words, j, end_flags)
                if d > hi + 1e-6:  # slightly too long (only when tolerance > 0)
                    cost += (d - hi) * TOLERANCE_PENALTY
                if d < lo - 1e-6:
                    if j == n:  # the final scene may be short
                        cost += (lo - d) * SHORT_LAST_PENALTY
                    elif d >= lo - tolerance - 1e-6:  # slightly too short (only when tolerance > 0)
                        cost += (lo - d) * TOLERANCE_PENALTY
                    else:
                        continue
                total = dp[i] + cost
                if total < dp[j]:
                    dp[j] = total
                    back[j] = i
        return dp, back

    attempts = [(min_len, max_len), (min_len - 1.0, max_len + 1.0), (min_len - 2.0, max_len + 2.0)]
    for lo, hi in attempts:
        dp, back = run(lo, hi)
        if dp[n] != float("inf"):
            if (lo, hi) != (min_len, max_len):
                print(f"⚠️  Could not fit strictly in {min_len}-{max_len}s (long silence in audio?). "
                      f"Relaxed to {lo}-{hi}s.")
            break
    else:
        print("❌ Could not segment the audio. Check for very long silences in the voiceover.")
        sys.exit(1)

    cuts = []
    j = n
    while j > 0:
        i = back[j]
        cuts.append((i, j, bounds[i], bounds[j]))
        j = i
    cuts.reverse()
    return cuts


# ----------------------------------------------------------------------------
# 4. Optional: use the written script for clean scene text
# ----------------------------------------------------------------------------
def load_script_tokens(path):
    raw = open(path, encoding="utf-8-sig").read()
    lines = [re.sub(r"^\s*(?:scene\s*)?\d+\s*[.:)\-]\s+", "", l, flags=re.I) for l in raw.splitlines()]
    text = " ".join(l.strip() for l in lines if l.strip())
    text = re.sub(r"([.!?])(?=[A-Z])", r"\1 ", text)  # "back.The" -> "back. The"
    return text.split()


def align_script(words, script_tokens):
    """For each Whisper word, the index of the matching script token (or -1)."""
    matched = [-1] * len(words)
    if not script_tokens:
        return matched
    wc = [clean_word(w["text"]) for w in words]
    sc = [clean_word(t) for t in script_tokens]
    sm = difflib.SequenceMatcher(None, wc, sc, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            matched[blk.a + k] = blk.b + k
    return matched


def sentence_end_flags(words, script_tokens, matched):
    """
    True for each word that finishes a sentence. If a written script is given, ITS punctuation
    wins (you control where sentences end: add a full stop in the script to create a cut point).
    Words Whisper heard but the script can't match fall back to Whisper's own punctuation.
    """
    end_re = re.compile(r"[.!?][\"')\]]*$")
    flags = []
    for i, w in enumerate(words):
        if script_tokens and matched[i] != -1:
            flags.append(bool(end_re.search(script_tokens[matched[i]])))
        else:
            flags.append(bool(end_re.search(w["text"])))
    return flags


def scene_texts(words, cuts, script_tokens, matched):
    if not script_tokens:
        return [" ".join(w["text"] for w in words[a:b]) for a, b, _, _ in cuts]

    starts = []
    prev = 0
    for a, b, _, _ in cuts:
        idx = next((matched[x] for x in range(a, b) if matched[x] != -1), prev)
        idx = max(idx, prev)
        starts.append(idx)
        prev = idx
    starts[0] = 0
    starts.append(len(script_tokens))
    return [" ".join(script_tokens[starts[k]:starts[k + 1]]) or
            " ".join(w["text"] for w in words[cuts[k][0]:cuts[k][1]])
            for k in range(len(cuts))]


# ----------------------------------------------------------------------------
# 5. Build manifest + Flow plan
# ----------------------------------------------------------------------------
def pick_clip(duration, clip_lengths):
    """Clip length whose playback speed is closest to 1.0x (log-scale, so 0.9x ~ 1.11x)."""
    return min(clip_lengths, key=lambda c: abs(math.log(c / duration)))


def build(audio_path, script_path, out_json, args):
    clip_lengths = sorted(float(c) for c in args.clips.split(","))
    words = get_words(audio_path, args.model)
    if not words:
        print("❌ Whisper returned no words.")
        sys.exit(1)

    tokens = load_script_tokens(script_path) if script_path else None
    matched = align_script(words, tokens)
    end_flags = sentence_end_flags(words, tokens, matched)
    cuts = split_scenes(words, args.min, args.max, args.target, end_flags, args.tolerance)
    texts = scene_texts(words, cuts, tokens, matched)

    scenes = []
    for k, ((_, last, s, e), text) in enumerate(zip(cuts, texts), start=1):
        ends_on = "end-of-audio" if last == len(words) else cut_kind(words, last, end_flags)
        dur = round(e - s, 4)
        planned = pick_clip(dur, clip_lengths)
        clip_file = os.path.join(PUBLIC_DIR, f"Scene{k:02d}.mp4")
        actual = probe_duration(clip_file) if os.path.exists(clip_file) else None
        scenes.append({
            "scene_id": k,
            "text": text,
            "startSec": round(s, 4),
            "endSec": round(e, 4),
            "durationSec": dur,
            "endsOn": ends_on,
            "flowClipSec": int(planned) if planned == int(planned) else planned,
            "videoDurationSec": actual if actual else planned,
            "clipFound": bool(actual),
        })

    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    json.dump(scenes, open(out_json, "w", encoding="utf-8"), indent=4, ensure_ascii=False)

    # ---- human-readable plan: this is what you feed into Flow ----
    lines = []
    for sc in scenes:
        rate = sc["videoDurationSec"] / sc["durationSec"]
        flag = "" if 0.8 <= rate <= 1.25 else "  ⚠️ speed"
        if sc["endsOn"] in ("comma", "mid"):
            flag += f"  ⚠️ cut on {sc['endsOn']}, not a full stop"
        lines.append(f"{sc['scene_id']}. [{sc['durationSec']:.2f}s -> generate {sc['flowClipSec']}s clip | "
                     f"playback {rate:.2f}x{flag}] {sc['text']}")
    open(PLAN_PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # ---- summary ----
    durs = [s["durationSec"] for s in scenes]
    by_len = {}
    for s in scenes:
        by_len[s["flowClipSec"]] = by_len.get(s["flowClipSec"], 0) + 1
    print("\n" + "=" * 58)
    print(f"Scenes: {len(scenes)} | total {scenes[-1]['endSec']:.1f}s | "
          f"avg {sum(durs)/len(durs):.2f}s | min {min(durs):.2f}s | max {max(durs):.2f}s")
    print("Flow clips to generate: " + ", ".join(f"{c} x {l}s" for l, c in sorted(by_len.items())))
    found = sum(1 for s in scenes if s["clipFound"])
    print(f"Clips in public/: {found}/{len(scenes)}")
    if 0 < found != len(scenes):
        missing = [s["scene_id"] for s in scenes if not s["clipFound"]]
        print(f"⚠️  Missing clips for scenes: {missing[:15]}{' ...' if len(missing) > 15 else ''}")
    forced = [s for s in scenes if s["endsOn"] in ("comma", "mid")]
    full = sum(1 for s in scenes if s["endsOn"] in ("sentence", "end-of-audio"))
    print(f"Scenes ending on a full sentence: {full}/{len(scenes)}")
    if forced:
        print(f"⚠️  {len(forced)} scene(s) could not end on a full stop within {args.min:g}-{args.max:g}s: "
              f"{[s['scene_id'] for s in forced][:15]}")
        print("   Fix: add a full stop in your script where the narrator naturally pauses, then re-run.")
    off = [s for s in scenes if not (0.8 <= s["videoDurationSec"] / s["durationSec"] <= 1.25)]
    if off:
        print(f"⚠️  {len(off)} scene(s) would play outside 0.8x-1.25x (see flow_plan.txt)")
    print(f"✅ Manifest: {out_json}")
    print(f"✅ Flow plan: {PLAN_PATH}")
    print("=" * 58)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Split voiceover into 5-8s Google Flow scenes")
    ap.add_argument("asset_dir")
    ap.add_argument("output", nargs="?", default=os.path.join(PROJECT_ROOT, "src", "remotion_manifest.json"))
    ap.add_argument("--min", type=float, default=5.0)
    ap.add_argument("--max", type=float, default=8.0)
    ap.add_argument("--target", type=float, default=6.0)
    ap.add_argument("--tolerance", type=float, default=0.0,
                    help="allow scenes this many seconds outside min/max (e.g. 0.5) instead of cutting mid-sentence")
    ap.add_argument("--clips", default="6,8")
    ap.add_argument("--model", default="base")
    a = ap.parse_args()

    audio, script = prepare_assets(a.asset_dir)
    build(audio, script, a.output, a)
