#!/usr/bin/env python3
"""Prepare lecture transcripts and attachments for Gemini.

Requirements:
  - whisper.cpp (``whisper-cli`` or ``main``)
  - ffmpeg, to extract audio from MP4 files

The model can be supplied with ``--model`` or through the
``WHISPER_MODEL`` environment variable.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path


# Set the language used both by Whisper and for the generated study notes.
# Supported values are "it" (Italian) and "en" (English).
NOTES_LANGUAGE = "en"
LANGUAGE_NAMES = {"it": "Italian", "en": "English"}


# This prompt tells Gemini how to combine the transcript with the PDF material.
PROMPT = rf"""Act as a university professor and a creator of exceptionally high-quality academic notes. Your task is to generate study notes.

Write the complete study notes in {LANGUAGE_NAMES.get(NOTES_LANGUAGE, NOTES_LANGUAGE)}.

Attached to this prompt are:
1. A TXT file: the transcript of the recorded lecture.
2. PDF files: reference notes taken during the same lecture and the professor's slides / handouts.

WARNING - RULES ABOUT THE SCOPE OF THE TOPICS:
The professor's slides are very broad and may cover the entire course. YOU MUST COVER EXCLUSIVELY THE TOPICS ACTUALLY EXPLAINED IN THE RECORDED LECTURE (the TXT file).
Use the transcript (TXT) not merely as a "compass" for identifying topics, but ACTIVELY EXTRACT AND INCLUDE in the summary the explanations, anecdotes, important details, and examples given orally by the professor.
Use the slides and the reference notes to supplement and formalize these oral explanations with the correct formulas and exact diagrams. Completely ignore any parts of the slides and documents that were NOT discussed in the recording.

Your goal is to produce a long, discursive, and detailed summary suitable for thorough university-level study. Include every important detail by combining and integrating the oral explanations (from the TXT file) with the concepts, formulas, diagrams, and slides (from the PDFs), generating proofs, graphs (in ASCII art), and so on where appropriate.

You MUST strictly follow the style, structure, and pedagogical depth described in the following rules. The notes must be suitable both for preparing for an oral exam and for understanding the concepts in depth.

FORMATTING AND STYLE RULES:

1. HEADING STRUCTURE:
   - Use `##` for main topics.
   - Use `###` or `####` for subtopics and proofs.

2. FORMULAS AND MATHEMATICS:
   - Use LaTeX rigorously for mathematics.
   - Use `$` for inline formulas and `$$` for block formulas.
   - ALWAYS explain the logical, physical, or mathematical meaning of the terms in a formula.

3. STEP-BY-STEP PROOFS AND REASONING:
   - Do not skip algebraic or logical steps.
   - Whenever moving from one equation or logical step to another, explain what is being done.

4. ASCII DIAGRAMS AND REFERENCES:
   - For every electromagnetic configuration, electrical circuit, or visual concept, create a diagram in an ASCII-art code block.
   - Immediately before every ASCII block, insert an italicized textual reference that clearly indicates its source.

5. ORAL-EXAM SENTENCES AND DEFINITIONS:
   - Include a line "* **Oral-exam sentence:** *" with a rigorous summary.
   - Use blockquotes for fundamental definitions, theorems, and important physical conclusions.

6. PATTERNS, USEFUL TRICKS, AND THE PROFESSOR'S TIPS:
   - Highlight advice, tricks, and specific details with "💡 **PROFESSOR'S TIP:**".

7. TONE:
   - Objective, impersonal, and textbook-like. Do not address the reader directly.

8. The very last line must be reserved for service announcements. If there are none:
   "*No service announcements were recorded in this lecture.*"
"""


def run(command: list[str], *, description: str) -> None:
    """Run an external command and turn common failures into clear errors."""
    print(f"[>] {description}")
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Command not found: {command[0]!r}. Install it or provide its path."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed ({exc.returncode}): {' '.join(command)}") from exc


def transcribe(
    videos: list[Path], whisper_cli: str, model: Path, output_dir: Path
) -> list[Path]:
    """Extract audio from each video and create a TXT transcript with whisper.cpp."""
    transcripts: list[Path] = []

    # Temporary WAV files are removed automatically after all videos are processed.
    with tempfile.TemporaryDirectory(prefix="lecture-audio-") as temporary:
        temporary_dir = Path(temporary)
        for video in videos:
            wav = temporary_dir / f"{video.stem}.wav"
            output_base = output_dir / video.stem

            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video),
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(wav),
                ],
                description=f"Extracting audio from {video.name}",
            )
            run(
                [
                    whisper_cli,
                    "-m",
                    str(model),
                    "-f",
                    str(wav),
                    "-l",
                    NOTES_LANGUAGE,
                    "-otxt",
                    "-of",
                    str(output_base),
                ],
                description=f"Transcribing {video.name} with whisper.cpp",
            )

            transcript = output_base.with_suffix(".txt")
            if not transcript.is_file():
                raise RuntimeError(
                    f"whisper.cpp did not create {transcript.name}."
                )
            transcripts.append(transcript)
    return transcripts


def set_clipboard(value: str, *, file_uri: bool = False) -> bool:
    """Set the clipboard using the tool available on the current operating system."""
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Set-Clipboard",
            "-Value",
            value,
        ]
    else:
        # wl-copy is preferred on Wayland; xclip and xsel cover X11 sessions.
        clipboard_command = (
            shutil.which("wl-copy")
            or shutil.which("xclip")
            or shutil.which("xsel")
        )
        if not clipboard_command:
            return False

        if Path(clipboard_command).name == "xclip":
            command = [clipboard_command, "-selection", "clipboard"]
            if file_uri:
                command += ["-t", "text/uri-list"]
        elif Path(clipboard_command).name == "xsel":
            command = [clipboard_command, "--clipboard", "--input"]
        else:
            command = [clipboard_command]

    try:
        subprocess.run(
            command,
            input=value,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def paste_in_browser() -> bool:
    """Simulate Ctrl+V when the desktop session allows it."""
    if os.name == "nt":
        return False

    # xdotool generally works under X11, but not reliably under Wayland.
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return False
    try:
        subprocess.run([xdotool, "key", "ctrl+v"], check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def open_gemini() -> bool:
    """Open Gemini in a new Firefox window, if Firefox is available."""
    firefox = shutil.which("firefox")
    if not firefox:
        return webbrowser.open_new("https://gemini.google.com/app")
    try:
        subprocess.Popen(
            [firefox, "--new-window", "https://gemini.google.com/app"],
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def main() -> int:
    if NOTES_LANGUAGE not in LANGUAGE_NAMES:
        valid_languages = ", ".join(sorted(LANGUAGE_NAMES))
        print(
            f"[ERROR] Unsupported NOTES_LANGUAGE: {NOTES_LANGUAGE!r}. "
            f"Use one of: {valid_languages}.",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--whisper-cli",
        default=(
            os.getenv("WHISPER_CPP")
            or shutil.which("whisper-cli")
            or shutil.which("main")
            or "whisper-cli"
        ),
        help=(
            "Path to the whisper.cpp executable. Defaults to WHISPER_CPP, "
            "whisper-cli, or main found in PATH."
        ),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(os.getenv("WHISPER_MODEL", "models/ggml-large-v3-turbo.bin")),
        help="Path to the whisper.cpp model file.",
    )
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Do not open Gemini or use the clipboard.",
    )
    args = parser.parse_args()

    # Resolve all input and output paths relative to the script directory.
    work_dir = Path(__file__).resolve().parent
    os.chdir(work_dir)
    if not args.model.is_absolute():
        args.model = work_dir / args.model

    videos = sorted(work_dir.glob("*.mp4"))
    pdfs = sorted(work_dir.glob("*.pdf"))

    if not videos:
        print("[ERROR] At least one .mp4 file is required.", file=sys.stderr)
        return 1
    if not args.model.is_file():
        print(
            f"[ERROR] Model not found: {args.model}\n"
            "Use --model /path/to/model.gguf or set WHISPER_MODEL.",
            file=sys.stderr,
        )
        return 1

    summary_dir = work_dir / "RIASSUNTO"
    summary_dir.mkdir(exist_ok=True)
    try:
        transcripts = transcribe(videos, args.whisper_cli, args.model, work_dir)
    except RuntimeError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    # Remove auxiliary whisper.cpp outputs that are not needed in the summary.
    for video in videos:
        for extension in (".vtt", ".tsv", ".json"):
            video.with_suffix(extension).unlink(missing_ok=True)

    # Join multiple lecture transcripts with a visible separator.
    combined = "\n\n[*******************]\n\n".join(
        transcript.read_text(encoding="utf-8", errors="replace").strip()
        for transcript in transcripts
    )
    (summary_dir / "Trascrizione_Unita.txt").write_text(
        combined + "\n",
        encoding="utf-8",
    )
    (summary_dir / "APPUNTI.md").write_text("", encoding="utf-8")

    # Move the reference PDFs into the same directory as the generated material.
    for pdf in pdfs:
        shutil.move(str(pdf), summary_dir / pdf.name)

    print(
        f"[OK] Combined {len(videos)} transcripts into "
        "RIASSUNTO/Trascrizione_Unita.txt"
    )
    print(PROMPT)

    if args.no_gemini:
        return 0

    if not open_gemini():
        print("[WARNING] Could not start Firefox automatically.")
    print(
        "[INFO] If Firefox remains in the background, bring it to the foreground "
        "before pasting."
    )
    time.sleep(10)

    # Paste each PDF as a file URI, then paste the combined transcript as text.
    for pdf in sorted(summary_dir.glob("*.pdf")):
        copied = set_clipboard(f"{pdf.resolve().as_uri()}\n", file_uri=True)
        pasted = copied and paste_in_browser()
        if not pasted:
            print(f"[WARNING] Could not paste {pdf.name} automatically.")
        time.sleep(10)

    copied = set_clipboard(
        (summary_dir / "Trascrizione_Unita.txt").read_text(encoding="utf-8")
    )
    if copied and not paste_in_browser():
        print("[WARNING] Transcript copied, but could not paste it automatically.")
    time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
