import sys
import os
import json
import subprocess
import tempfile
import shutil


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".wmv"}

OUTPUT_FPS = 24
TITLE_CARD_FRAMES = 36
TITLE_CARD_DURATION = TITLE_CARD_FRAMES / OUTPUT_FPS  # 1.5s

CONTENT_SHORT_FRAMES = 324   # 360 total - 36 title card
CONTENT_LONG_FRAMES  = 684   # 720 total - 36 title card
CONTENT_SHORT_SECONDS = CONTENT_SHORT_FRAMES / OUTPUT_FPS  # 13.5
CONTENT_LONG_SECONDS  = CONTENT_LONG_FRAMES  / OUTPUT_FPS  # 28.5


def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    with open(config_path) as f:
        config = json.load(f)
    titlecard = config.get("titlecard_path", "titlecard.png")
    if not os.path.isabs(titlecard):
        titlecard = os.path.join(script_dir, titlecard)
    return titlecard, config.get("output_suffix", "_final")


def run(cmd, **kwargs):
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed:\n{' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def probe_video(path):
    out = run([
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json",
        path
    ])
    import json as _json
    data = _json.loads(out)
    streams = data.get("streams", [])

    video = next((s for s in streams if s["codec_type"] == "video"), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)

    if not video:
        raise RuntimeError("No video stream found in the file.")

    w = int(video["width"])
    h = int(video["height"])
    pix_fmt = video.get("pix_fmt", "yuv420p")

    # framerate as fraction string e.g. "30000/1001"
    fps = video.get("r_frame_rate", "25/1")

    sample_rate = int(audio.get("sample_rate", 44100)) if audio else None
    channels = int(audio.get("channels", 2)) if audio else None
    audio_bitrate = int(audio.get("bit_rate", 192000)) if audio else None

    # prefer per-stream video bitrate; fall back to total container bitrate
    raw_vbr = video.get("bit_rate") or data.get("format", {}).get("bit_rate")
    video_bitrate = int(raw_vbr) if raw_vbr else None

    return w, h, pix_fmt, fps, sample_rate, channels, audio_bitrate, video_bitrate


def probe_duration(path):
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ])
    return float(out.strip())


def get_processing_params(duration):
    """
    Returns (trim_to, target_total, error_msg).
    trim_to is None if no trimming is needed.
    error_msg is set when the duration is in the invalid 21–27s range.
    """
    tc = TITLE_CARD_DURATION
    short = CONTENT_SHORT_SECONDS   # 13.5
    long_ = CONTENT_LONG_SECONDS    # 28.5

    if duration <= short:
        return None, duration + tc, None
    elif duration <= 20.0:
        return short, short + tc, None
    elif duration < 28.0:
        return None, None, (
            f"Video is {duration:.1f}s — not a valid length.\n"
            f"Expected: up to 20s (targets 360 frames / 15s) or 28s+ (targets 720 frames / 30s)."
        )
    elif duration < 29.0:
        return None, duration + tc, None
    else:
        return long_, long_ + tc, None


def build_titlecard_clip(png_path, w, h, pix_fmt, fps, sample_rate, channels, audio_bitrate, video_bitrate, out_path):
    scale_filter = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"format={pix_fmt}"
    )

    vbr_flags = ["-b:v", str(video_bitrate)] if video_bitrate else ["-crf", "18"]

    if sample_rate is not None:
        abr_flags = ["-b:a", str(audio_bitrate)] if audio_bitrate else ["-b:a", "192000"]
        run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", png_path,
            "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={'stereo' if channels == 2 else 'mono'}",
            "-vf", scale_filter,
            "-r", str(OUTPUT_FPS),
            "-c:v", "libx264", "-preset", "veryfast",
            *vbr_flags,
            "-c:a", "aac",
            *abr_flags,
            "-frames:v", str(TITLE_CARD_FRAMES),
            "-pix_fmt", pix_fmt,
            "-shortest",
            out_path
        ])
    else:
        run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", png_path,
            "-vf", scale_filter,
            "-r", str(OUTPUT_FPS),
            "-c:v", "libx264", "-preset", "veryfast",
            *vbr_flags,
            "-frames:v", str(TITLE_CARD_FRAMES),
            "-pix_fmt", pix_fmt,
            out_path
        ])


def concatenate(input_path, titlecard_clip, output_path, has_audio, video_bitrate, audio_bitrate, trim_to=None, content_duration=None):
    vbr_flags = ["-b:v", str(video_bitrate), "-preset", "veryfast"] if video_bitrate else ["-crf", "18", "-preset", "veryfast"]
    fade_start = (trim_to if trim_to is not None else content_duration) - 2

    if trim_to is not None:
        v_src = f"[0:v]trim=duration={trim_to},setpts=PTS-STARTPTS,fps={OUTPUT_FPS}[v0]"
        a_src = f"[0:a]atrim=duration={trim_to},asetpts=PTS-STARTPTS,afade=t=out:st={fade_start}:d=2[a0]"
        v_label, a_label = "[v0]", "[a0]"
    else:
        v_src = f"[0:v]fps={OUTPUT_FPS}[v0]"
        a_src = f"[0:a]afade=t=out:st={fade_start}:d=2[a0]"
        v_label, a_label = "[v0]", "[a0]"

    if has_audio:
        abr_flags = ["-b:a", str(audio_bitrate)] if audio_bitrate else ["-b:a", "192000"]
        parts = []
        if v_src:
            parts.append(v_src)
        parts.append(a_src)
        parts.append(f"{v_label}{a_label}[1:v][1:a]concat=n=2:v=1:a=1[v][a]")
        run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", titlecard_clip,
            "-filter_complex", ";".join(parts),
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            *vbr_flags,
            "-c:a", "aac",
            *abr_flags,
            output_path
        ])
    else:
        parts = []
        if v_src:
            parts.append(v_src)
        parts.append(f"{v_label}[1:v]concat=n=2:v=1[v]")
        run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", titlecard_clip,
            "-filter_complex", ";".join(parts),
            "-map", "[v]",
            "-c:v", "libx264",
            *vbr_flags,
            output_path
        ])


def open_folder(path):
    folder = os.path.dirname(os.path.abspath(path))
    subprocess.Popen(["explorer", folder])


def process_file(input_path, titlecard_path, suffix):
    """Process a single video file. Returns (output_path, error_msg)."""
    input_path = input_path.strip('"')

    ext = os.path.splitext(input_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None, f"'{ext}' is not a supported video format."

    if not os.path.isfile(input_path):
        return None, f"File not found: {input_path}"

    base, orig_ext = os.path.splitext(input_path)
    output_path = f"{base}{suffix}{orig_ext}"

    if os.path.abspath(output_path) == os.path.abspath(input_path):
        return None, "Output path would overwrite the input. Check your suffix config."

    print(f"Processing: {os.path.basename(input_path)}")
    print("  Reading video properties...")

    try:
        w, h, pix_fmt, fps, sample_rate, channels, audio_bitrate, video_bitrate = probe_video(input_path)
        duration = probe_duration(input_path)
    except RuntimeError as e:
        return None, str(e)

    print(f"  Resolution : {w}x{h}")
    print(f"  Frame rate : {fps}")
    print(f"  Duration   : {duration:.2f}s")
    print(f"  Bitrate    : {round(video_bitrate / 1_000_000, 1)} Mbps" if video_bitrate else "  Bitrate    : unknown")
    print(f"  Audio      : {'yes' if sample_rate else 'no'}")

    trim_to, target_total, error_msg = get_processing_params(duration)
    if error_msg:
        return None, error_msg

    if trim_to is not None:
        content_frames = round(trim_to * OUTPUT_FPS)
        total_frames = content_frames + TITLE_CARD_FRAMES
        print(f"  Action     : trim to {content_frames} frames + {TITLE_CARD_FRAMES} frame title card = {total_frames} frames ({target_total:.0f}s)")
    else:
        approx_frames = round(target_total * OUTPUT_FPS)
        print(f"  Action     : append {TITLE_CARD_FRAMES} frame title card (~{approx_frames} frames total / {target_total:.1f}s)")

    tmp_dir = tempfile.mkdtemp()
    try:
        titlecard_clip = os.path.join(tmp_dir, "titlecard_clip.mp4")

        print("  Building title card clip...")
        build_titlecard_clip(titlecard_path, w, h, pix_fmt, fps, sample_rate, channels, audio_bitrate, video_bitrate, titlecard_clip)

        print("  Concatenating video...")
        content_duration = trim_to if trim_to is not None else duration
        concatenate(input_path, titlecard_clip, output_path, has_audio=(sample_rate is not None), video_bitrate=video_bitrate, audio_bitrate=audio_bitrate, trim_to=trim_to, content_duration=content_duration)

    except RuntimeError as e:
        return None, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"  Saved: {output_path}\n")
    return output_path, None


def main():
    if len(sys.argv) < 2:
        print("Usage: drag one or more video files onto process_video.bat")
        print("       or: python process_video.py <video> [video2 ...]")
        input("\nPress Enter to close...")
        sys.exit(1)

    try:
        titlecard_path, suffix = load_config()
    except Exception as e:
        print(f"Error reading config.json: {e}")
        input("\nPress Enter to close...")
        sys.exit(1)

    if not os.path.isfile(titlecard_path):
        print(f"Error: Title card PNG not found at: {titlecard_path}")
        print("Place your titlecard.png in the same folder as this script.")
        input("\nPress Enter to close...")
        sys.exit(1)

    input_paths = sys.argv[1:]
    successes, failures = [], []

    for path in input_paths:
        output_path, error = process_file(path, titlecard_path, suffix)
        if error:
            print(f"  Error: {error}\n")
            failures.append((path, error))
        else:
            successes.append(output_path)

    print("-" * 50)
    print(f"Done. {len(successes)} succeeded, {len(failures)} failed.")
    if failures:
        print("\nFailed files:")
        for path, err in failures:
            print(f"  {os.path.basename(path)}: {err}")

    if successes:
        open_folder(successes[-1])

    input("\nPress Enter to close...")


if __name__ == "__main__":
    main()
