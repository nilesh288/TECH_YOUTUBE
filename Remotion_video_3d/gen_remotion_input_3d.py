import os
import sys
import json
import shutil
import glob
import re
import subprocess
import difflib
from faster_whisper import WhisperModel

PROJECT_ROOT = "/home/nilesh/Remotion_video_3d"
PUBLIC_DIR = os.path.join(PROJECT_ROOT, "public")

def prepare_assets(source_dir):
    print(f"Scanning source directory: {source_dir}")
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    
    txt_files = glob.glob(os.path.join(source_dir, "*.txt"))
    if not txt_files:
        print(f"❌ Error: No .txt file found in {source_dir}")
        sys.exit(1)
    scene_text_path = os.path.join(PROJECT_ROOT, "scenes.txt")
    shutil.copy2(txt_files[0], scene_text_path)
    print(f"✅ Copied script: {os.path.basename(txt_files[0])} -> scenes.txt")

    mp3_files = glob.glob(os.path.join(source_dir, "*.mp3"))
    if not mp3_files:
        print(f"❌ Error: No .mp3 file found in {source_dir}")
        sys.exit(1)
    audio_path = os.path.join(PUBLIC_DIR, "voice_over.mp3")
    shutil.copy2(mp3_files[0], audio_path)
    print(f"✅ Copied audio: {os.path.basename(mp3_files[0])} -> public/voice_over.mp3")

    mp4_files = glob.glob(os.path.join(source_dir, "*.mp4"))
    if not mp4_files:
        print(f"❌ Error: No .mp4 files found in {source_dir}")
        sys.exit(1)
    
    mp4_files.sort(key=lambda f: os.path.basename(f))
    
    # Check for existing animations in public/
    existing_scenes = glob.glob(os.path.join(PUBLIC_DIR, "Scene*.mp4"))
    copy_animations = True
    
    if existing_scenes:
        print(f"\n⚠️  Found {len(existing_scenes)} animation files already in the public/ folder.")
        ans = input("Do you want to overwrite them with files from the main source? (y/n): ")
        if ans.strip().lower() != 'y':
            copy_animations = False
            print("⏩ Skipping animation file copy. Using existing files in public/.")
            
    if copy_animations:
        print("Copying and renaming video files...")
        for file_path in mp4_files:
            filename = os.path.basename(file_path)
            match = re.match(r'^(\d+)_', filename)
            if match:
                scene_num = int(match.group(1))
                new_filename = f"Scene{str(scene_num).zfill(2)}.mp4"
                dest_path = os.path.join(PUBLIC_DIR, new_filename)
                shutil.copy2(file_path, dest_path)
                print(f"   -> Copied {new_filename}")
            
    return audio_path, scene_text_path

def get_video_duration(filepath):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Warning: Could not read duration for {filepath}. Defaulting to 6.0s")
        return 6.0

def parse_scenes_text(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read().lstrip('\ufeff').strip()
        
    scenes = []
    pattern = re.compile(r'^(?:Scene\s*)?(\d+)[\.\:\-]?\s+(.*?)(?=^(?:Scene\s*)?\d+[\.\:\-]?\s+|\Z)', re.DOTALL | re.IGNORECASE | re.MULTILINE)
    matches = pattern.findall(text)
    
    if matches:
        for match in matches:
            scenes.append({"scene_id": int(match[0]), "text": match[1].strip()})
    else:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            scenes.append({"scene_id": i+1, "text": line})
            
    return scenes

def clean_word(w):
    return re.sub(r'[^a-z0-9]', '', w.lower())

def generate_remotion_manifest(audio_path, scene_text_path, output_json_path):
    print("\n==========================================")
    print("Running Global Acoustic Alignment (Faster-Whisper)")
    print("==========================================")
    
    scenes = parse_scenes_text(scene_text_path)
    if not scenes:
        print("❌ Error: Could not parse any text from scenes.txt")
        sys.exit(1)

    print("Loading AI Model into RAM...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    
    print("Transcribing and extracting frame-perfect word timestamps...")
    segments, _ = model.transcribe(audio_path, beam_size=5, word_timestamps=True, language="en")
    
    whisper_words = []
    for segment in segments:
        for word in segment.words:
            cleaned = clean_word(word.word)
            if cleaned:
                whisper_words.append({
                    "clean": cleaned,
                    "start": word.start,
                    "end": word.end
                })

    print("Mapping transcript globally to script scenes...")
    
    s_words = []
    s_word_to_scene = []
    for scene in scenes:
        words = [clean_word(w) for w in scene['text'].split() if clean_word(w)]
        for w in words:
            s_words.append(w)
            s_word_to_scene.append(scene['scene_id'])

    w_words = [w['clean'] for w in whisper_words]
    
    w_scene_assignments = [-1] * len(w_words)
    sm = difflib.SequenceMatcher(None, w_words, s_words)
    
    for block in sm.get_matching_blocks():
        for i in range(block.size):
            w_idx = block.a + i
            s_idx = block.b + i
            w_scene_assignments[w_idx] = s_word_to_scene[s_idx]
            
    for i in range(len(w_scene_assignments)):
        if w_scene_assignments[i] == -1:
            left_scene, right_scene = -1, -1
            left_dist, right_dist = 9999, 9999
            
            for j in range(i-1, -1, -1):
                if w_scene_assignments[j] != -1:
                    left_scene = w_scene_assignments[j]
                    left_dist = i - j
                    break
                    
            for j in range(i+1, len(w_scene_assignments)):
                if w_scene_assignments[j] != -1:
                    right_scene = w_scene_assignments[j]
                    right_dist = j - i
                    break
                    
            if left_scene != -1 and right_scene != -1:
                w_scene_assignments[i] = left_scene if left_dist <= right_dist else right_scene
            elif left_scene != -1:
                w_scene_assignments[i] = left_scene
            elif right_scene != -1:
                w_scene_assignments[i] = right_scene
            else:
                w_scene_assignments[i] = scenes[0]['scene_id']

    scene_times = {}
    for i, w in enumerate(whisper_words):
        sid = w_scene_assignments[i]
        if sid not in scene_times:
            scene_times[sid] = {'start': w['start'], 'end': w['end']}
        else:
            scene_times[sid]['end'] = w['end']
            
    for idx, scene in enumerate(scenes):
        sid = scene['scene_id']
        if sid in scene_times:
            scene['startSec'] = scene_times[sid]['start']
            scene['endSec'] = scene_times[sid]['end']
        else:
            scene['startSec'] = scenes[idx-1]['endSec'] if idx > 0 else 0.0
            scene['endSec'] = scene['startSec'] + 2.0
            
    for i in range(len(scenes) - 1):
        midpoint = (scenes[i]['endSec'] + scenes[i+1]['startSec']) / 2.0
        scenes[i]['endSec'] = round(midpoint, 2)
        scenes[i+1]['startSec'] = round(midpoint, 2)
        
    for scene in scenes:
        scene['durationSec'] = round(scene['endSec'] - scene['startSec'], 2)
        
    print("\nProbing original video lengths for slow-motion math...")
    for scene in scenes:
        scene_id = scene.get("scene_id")
        video_filename = f"Scene{str(scene_id).zfill(2)}.mp4"
        video_path = os.path.join(PUBLIC_DIR, video_filename)
        
        if os.path.exists(video_path):
            scene["videoDurationSec"] = get_video_duration(video_path)
        else:
            scene["videoDurationSec"] = 6.0
            
    os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as json_file:
        json.dump(scenes, json_file, indent=4, ensure_ascii=False)
        
    print(f"\n✅ Success! Frame-perfect manifest saved to: {output_json_path}")
    print(f"Total scenes mapped: {len(scenes)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 gen_remotion_input_3d.py <path_to_raw_asset_folder> [output.json]")
        sys.exit(1)
        
    RAW_ASSET_DIR = sys.argv[1]
    OUTPUT_JSON = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(PROJECT_ROOT, "src", "remotion_manifest.json")
        
    print("==========================================")
    print("Ingesting Files and Building Manifest")
    print("==========================================")
    
    final_audio, final_script = prepare_assets(RAW_ASSET_DIR)
    generate_remotion_manifest(final_audio, final_script, OUTPUT_JSON)
