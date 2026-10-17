import sys
import time
from faster_whisper import WhisperModel

def test_whisper_timestamps(audio_path):
    print("==========================================")
    print("Loading Faster-Whisper (base.en) on CPU...")
    print("==========================================")
    
    # We use 'int8' compute type which is highly optimized for the Pi's CPU
    try:
        model = WhisperModel("base", device="cpu", compute_type="int8")
    except Exception as e:
        print(f"Error loading model: {e}")
        sys.exit(1)

    print(f"\nTranscribing: {audio_path}")
    print("This will take a moment, especially on the first run as it downloads the ~140MB model.\n")
    
    start_time = time.time()
    
    # beam_size=5 provides a good balance between speed and accuracy
    segments, info = model.transcribe(audio_path, beam_size=5, language="en")
    
    print(f"Detected language: {info.language} (Confidence: {info.language_probability:.2f})")
    print("------------------------------------------")
    print("TIMESTAMP OUTPUT:")
    print("------------------------------------------")
    
    # The generator yields segments one by one as they are transcribed
    for segment in segments:
        print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
        
    end_time = time.time()
    print("------------------------------------------")
    print(f"Transcription finished in {end_time - start_time:.2f} seconds.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 whisper_poc.py <audio_file.mp3>")
        sys.exit(1)
        
    AUDIO_FILE = sys.argv[1]
    test_whisper_timestamps(AUDIO_FILE)
