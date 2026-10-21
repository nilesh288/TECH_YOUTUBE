#!/bin/bash
set -e # Exit immediately if any command fails

echo "=========================================="
echo "Starting YourTechLab Build Pipeline (3D)"
echo "=========================================="

# --- Pre-flight Checks ---
echo "Running pre-flight checks..."

for cmd in python3 jq bc ffmpeg ffprobe npx; do
  if ! command -v $cmd &> /dev/null; then
    echo "❌ Error: Required command '$cmd' is not installed."
    exit 1
  fi
done

if [ ! -f "gen_remotion_input_3d.py" ]; then
  echo "❌ Error: 'gen_remotion_input_3d.py' is missing from the current directory!"
  exit 1
fi
echo "✅ All pre-flight checks passed!"
echo ""

# --- Step 1: Generate JSON Manifest ---
echo "Step 1: Manifest Generation"

generate_manifest() {
  echo ""
  read -p "Alignment JSON [public/scene_alignment.json]: " ALIGN_PATH
  ALIGN_PATH=${ALIGN_PATH:-public/scene_alignment.json}
  read -p "Folder with Shot_001.mp4, ... [../flow-automation/videos]: " VIDEO_DIR
  VIDEO_DIR=${VIDEO_DIR:-../flow-automation/videos}

  echo "Generating JSON Manifest..."
  if ! python3 gen_remotion_input_3d.py \
      --alignment "$ALIGN_PATH" \
      --video-dir "$VIDEO_DIR" \
      --output src/remotion_manifest.json; then
    echo "❌ Error: Python script failed."
    exit 1
  fi
}

if [ -f "src/remotion_manifest.json" ]; then
  read -p "A manifest file already exists. Do you want to generate a NEW one? (y/n): " REGEN_MANIFEST
  if [[ "$REGEN_MANIFEST" =~ ^[Yy]$ ]]; then
    generate_manifest
  else
    echo "Skipping manifest generation, using existing file."
  fi
else
  generate_manifest
fi

if [ ! -f "src/remotion_manifest.json" ]; then
  echo "❌ Error: 'src/remotion_manifest.json' was not generated."
  exit 1
fi

# --- Voice-over check (a longer MP3 is fine; the final mux uses it only up to the manifest end) ---
if [ ! -f "public/voice_over.mp3" ]; then
  echo "❌ Error: public/voice_over.mp3 not found. Copy this episode's narration there first."
  exit 1
fi
AUDIO_SEC=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 public/voice_over.mp3)
MANIFEST_END=$(jq '.[-1].endSec' src/remotion_manifest.json)
if [ "$(echo "$AUDIO_SEC < $MANIFEST_END - 0.5" | bc)" -eq 1 ]; then
  echo "❌ Error: public/voice_over.mp3 is ${AUDIO_SEC}s but the manifest needs ${MANIFEST_END}s."
  echo "   The narration is shorter than the timeline."
  exit 1
fi
echo "✅ Voice-over OK (${AUDIO_SEC}s). Only the first ${MANIFEST_END}s will be used; the rest is dropped."

# --- Step 2: Calculate frame count dynamically ---
echo ""
echo "Step 2: Calculating frames & scenes..."
END_SEC=$(jq '.[-1].endSec' src/remotion_manifest.json)
TOTAL_SCENES=$(jq 'length' src/remotion_manifest.json)

if [ -z "$END_SEC" ] || [ "$END_SEC" == "null" ]; then
    echo "❌ Error: Could not read timestamps from the JSON. The Gemini output might be invalid."
    exit 1
fi

TOTAL_FRAMES=$(echo "$END_SEC * 30 + 60" | bc | awk '{print int($1+0.5)}')

# --- Step 3: Check for Background Music ---
if [ -f "public/background_music.mp3" ]; then
  echo "🎵 Background music found. It will be included in the render."
  BG_PROPS='{"hasBackgroundMusic":true}'
else
  echo "🔇 No background music found in public/. Proceeding without it."
  BG_PROPS='{"hasBackgroundMusic":false}'
fi

# --- Step 4: Determine number of parts ---
if [ -z "$1" ]; then
  echo ""
  echo "------------------------------------------"
  echo "Total Scenes: $TOTAL_SCENES"
  echo "Total video length: $TOTAL_FRAMES frames."
  echo "------------------------------------------"
  echo "How many parts would you like to split this render into?"
  echo "Options: 1, 2, 3, 4, or 5"
  read -p "Enter number of parts: " NUM_PARTS
else
  NUM_PARTS=$1
fi

if ! [[ "$NUM_PARTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "❌ Error: Please provide a valid integer (1-5)."
  exit 1
fi

echo ""
echo "Proceeding to render $TOTAL_SCENES scenes in $NUM_PARTS chunk(s)..."

export TMPDIR=/dev/shm
export NODE_OPTIONS="--max-old-space-size=4096"

mkdir -p out
> out/wav_list.txt
> out/stitched_temp.264

# --- Step 5: Render each dynamic chunk by SCENE boundaries ---
echo "Step 5: Rendering parts..."
for (( i=1; i<=NUM_PARTS; i++ ))
do
  START_SCENE_IDX=$(( (i - 1) * TOTAL_SCENES / NUM_PARTS ))
  END_SCENE_IDX=$(( (i * TOTAL_SCENES / NUM_PARTS) - 1 ))

  if [ "$i" -eq 1 ]; then
    START_FRAME=0
  else
    START_SEC=$(jq -r ".[$START_SCENE_IDX].startSec" src/remotion_manifest.json)
    START_FRAME=$(echo "$START_SEC * 30" | bc | awk '{print int($1+0.5)}')
  fi

  if [ "$i" -eq "$NUM_PARTS" ]; then
    END_FRAME=$(( TOTAL_FRAMES - 1 )) 
  else
    END_SEC=$(jq -r ".[$END_SCENE_IDX].endSec" src/remotion_manifest.json)
    END_FRAME=$(echo "$END_SEC * 30" | bc | awk '{print int($1+0.5)}')
    END_FRAME=$(( END_FRAME - 1 )) 
  fi

  ACTUAL_START_SCENE=$((START_SCENE_IDX + 1))
  ACTUAL_END_SCENE=$((END_SCENE_IDX + 1))

  echo "------------------------------------------"
  echo "Rendering Part $i of $NUM_PARTS (Scenes $ACTUAL_START_SCENE to $ACTUAL_END_SCENE | Frames: $START_FRAME to $END_FRAME)"
  echo "------------------------------------------"

  if ! npx remotion render YourTechLab-Episode "out/part_${i}.mp4" --frames="${START_FRAME}-${END_FRAME}" --concurrency=2 --props="$BG_PROPS"; then
     echo "❌ Error: Rendering part $i failed! Check the Remotion logs above."
     exit 1
  fi

  echo "Extracting raw streams to prevent AAC padding sync drift..."
  
  # Extract video to raw bitstream (removes MP4 container gap padding)
  ffmpeg -loglevel error -y -i "out/part_${i}.mp4" -c:v copy -bsf:v h264_mp4toannexb -f h264 "out/part_${i}.264"
  cat "out/part_${i}.264" >> out/stitched_temp.264
  
  # Extract audio to uncompressed WAV. If track is completely missing/muted, inject a silent filler!
  if ! ffmpeg -loglevel error -y -i "out/part_${i}.mp4" -vn -ac 2 -ar 48000 -c:a pcm_s16le "out/part_${i}.wav" 2>/dev/null; then
    echo "   -> No audio stream found in Part $i. Generating silent filler track to maintain sync..."
    CHUNK_DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "out/part_${i}.mp4")
    ffmpeg -loglevel error -y -f lavfi -i anullsrc=r=48000:cl=stereo -t "$CHUNK_DUR" -c:a pcm_s16le "out/part_${i}.wav"
  fi
  
  echo "file 'part_${i}.wav'" >> out/wav_list.txt
  
  # Clean up temp chunks immediately to save RAM disk space
  rm "out/part_${i}.mp4"
  rm "out/part_${i}.264"
done

# --- Step 6: Lossless Gapless Stitching ---
echo "Step 6: Gapless stream muxing..."
if ! ffmpeg -y -f concat -safe 0 -i out/wav_list.txt -c copy out/stitched_temp.wav; then
  echo "❌ Error: FFmpeg failed to stitch the audio chunks."
  exit 1
fi

if ! ffmpeg -y -fflags +genpts -r 30 -i out/stitched_temp.264 -i out/stitched_temp.wav -c:v copy -c:a aac -b:a 192k out/stitched_temp.mp4; then
  echo "❌ Error: FFmpeg failed to mux the final stitched video."
  exit 1
fi

echo "Step 7: Multiplexing perfectly synced master audio..."
if [ "$BG_PROPS" == '{"hasBackgroundMusic":true}' ]; then
  if ! ffmpeg -y -i out/stitched_temp.mp4 -t "$MANIFEST_END" -i public/voice_over.mp3 -i public/background_music.mp3 -filter_complex "[0:a]volume=3.0[a0];[1:a]volume=3.0[a1];[2:a]volume=0.3[bg];[a0][a1][bg]amix=inputs=3:duration=first[a]" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest out/Final_YourTechLab_Episode.mp4; then
     echo "❌ Error: Audio sync failed."
     exit 1
  fi
else
  if ! ffmpeg -y -i out/stitched_temp.mp4 -t "$MANIFEST_END" -i public/voice_over.mp3 -filter_complex "[0:a]volume=2.0[a0];[1:a]volume=2.0[a1];[a0][a1]amix=inputs=2:duration=first[a]" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest out/Final_YourTechLab_Episode.mp4; then
     echo "❌ Error: Audio sync failed."
     exit 1
  fi
fi

# Clean up all raw intermediate files
rm out/stitched_temp.mp4
rm out/stitched_temp.264
rm out/stitched_temp.wav
rm out/part_*.wav
rm out/wav_list.txt

echo "=========================================="
echo "🎉 Pipeline Complete! perfectly synced file saved to out/Final_YourTechLab_Episode.mp4"
echo "=========================================="
