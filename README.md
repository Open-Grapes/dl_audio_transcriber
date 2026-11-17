# dl_audio_transcriber

<img src="assets/icon.png" width="96" align="right" alt="dl_transcribe icon">

Turns voice memos, meetings, lectures and videos into text and subtitles, **fully offline** on your PC.
No length limit, no upload, no account. Uses Whisper (via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)) on your NVIDIA GPU, or the CPU if there's no NVIDIA card.

- Drop **any audio or video**: mp3, wav, m4a, aac, flac, ogg, opus, wma, mp4, mov, mkv, avi, webm, m4v, mts … The audio track is pulled out of videos automatically, no conversion step.
- Drop **folders** too: every media file inside is queued.
- Exports **PDF** and **TXT** (readable paragraphs, optional `(0:03)` timestamps), **SRT** / **VTT** subtitles (re-cut from word timings into ≤2-line cues), **JSON** (segments + word timings + confidence), **CSV**.
- Built-in **player**: play / pause, seek bar, volume. **Click any sentence** to play from there; the sentence being spoken is highlighted.
- **Edit transcript** in place before exporting, and **Save audio as MP3** from any audio or video file.
- **Find** (Ctrl+F, F3 for next) highlights every match in the transcript. **Show speakers** hides or shows the speaker names (on screen and in exports).
- **Check for updates** (top right) compares this version with the latest release on GitHub and, with **Update all**, downloads it, swaps the files when the app closes and restarts it. It also updates downloaded models. Settings and models are kept.
- **Speakers** (optional): tick *Speakers (who said what)* to label each turn *Speaker 1*, *Speaker 2* … (Auto finds how many, or pick the number). Click a speaker name to rename it everywhere or to move a wrongly labelled part to another speaker. Runs offline with sherpa-onnx (pyannote segmentation 3.0, MIT + 3D-Speaker ERes2Net, Apache-2.0; ~45 MB, included).
- **99 languages**, auto-detected — including English, Mandarin, Cantonese, Malay, Tamil, Indonesian, Thai, Vietnamese, Tagalog, Japanese, Korean, Hindi.
- **Translate to English** from any of them.
- **Mixed languages**: tick this for recordings that switch language (e.g. English ↔ Mandarin); language is detected per section instead of once.
- **Vocabulary** box: type names, jargon or spellings (e.g. `Damien, Tanjong Pagar, CPF, Kubernetes`) to steer spelling.
- **Windows 10 / 11** (64-bit) and **macOS 14+ on Apple Silicon** (M1 or newer).

## Download

From the [latest release](https://github.com/Open-Grapes/dl_audio_transcriber/releases/latest):

| Computer | File | How |
|---|---|---|
| Windows 10 / 11 (64-bit) | `dl_transcribe-windows.zip` | Unzip anywhere and run **`dl_transcribe.exe`**. SmartScreen may warn (the app isn't code-signed): **More info → Run anyway** |
| macOS 14+ on Apple Silicon (M1 or newer) | `dl_transcribe-mac-apple-silicon.dmg` | Open the .dmg and drag the app to Applications. First launch: System Settings → Privacy & Security → **Open Anyway** (the app isn't notarized by Apple) |

No install, no Python. The first time you press Start it offers to download a speech model (one time; after that it works offline).

On a Mac the app runs on the processor (Macs have no NVIDIA GPU): *small* or *base* are quick, *large-v3-turbo* is more accurate but slower. Shortcuts use Cmd instead of Ctrl (Cmd+F to find). Models and settings live in `~/Library/Application Support/dl_transcribe`.

## Run

From source (this folder):

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
dl_transcribe.cmd
```

Or build the stand-alone app (no Python needed on the target PC):

```bat
.venv\Scripts\python build.py
```

→ `dist\Windows\dl_transcribe\dl_transcribe.exe` (+ `dl_transcribe-windows.zip`). The NVIDIA CUDA libraries are bundled, so only a normal NVIDIA driver is needed.

## Use

1. Drop files on the window (or on `dl_transcribe.exe` itself), or click the drop area to browse. Nothing runs yet.
2. Press **Start**. Text appears as it's recognised.
3. Click a row to see its transcript. Click any sentence to play it (Space or double-clicking the row plays / pauses). **Show timestamps** switches the `(0:03)` stamps on or off. **Edit transcript** lets you type straight into the transcript (timestamps and speaker names stay locked); **Save edits** keeps the changes.
4. Tick the formats on the **Save as** row and press **Export**. It exports the selected rows, or every finished file if none are selected. Files go to a **Transcripts** folder beside each original (`name.pdf`, `name.srt` …, translations as `name_en.*`). **Save to…** picks one folder for everything; **Reset save folder** (bottom) goes back to the default. Exporting again replaces the earlier export, so edits are picked up. **Open folder** shows the exported files.
5. **Save audio as MP3** writes `name.mp3` to the same folder, from any audio or video file.

Closing the window with transcripts that were never exported asks first.

The progress bar and the row show the same percentage, and the status line shows the elapsed time. While it runs, **Start** turns into a red **Stop** button that stops the current file straight away. Transcription runs in a separate background process, so the window never freezes.

### Models

None are included in the download. The first time you press Start with a model that isn't on the PC yet, the app asks: **Download <model>** (just that one) or **Download all** (every model, ~6.9 GB, so you can switch later without internet). Models are saved to `%LOCALAPPDATA%\dl_transcribe\models`. After that, everything runs offline.

Pick a model from the **Model** drop list (downloaded ones are marked ✓), or press **Models…** to manage them: see speed, accuracy, size and status for each one; **Download recommended / selected / all**; **Check for updates** and **Install updates**; **Remove selected / all**; **Use selected model**; and **Open folder**. Downloads there can be cancelled and resume where they stopped.

### GPU on other PCs

Nothing to install or configure. The app checks the PC every time it starts (shown top right):

- **NVIDIA card** (GTX 900 series or newer) with a driver from the last ~2 years: uses the GPU. The CUDA libraries are inside the app; only the normal NVIDIA driver is needed (every gaming PC already has it).
- **NVIDIA card with an old driver**: runs on the CPU and shows a note to update the driver at nvidia.com/drivers.
- **AMD / Intel graphics, or no graphics card**: runs on the CPU (works, just slower — pick *small* or *base*).
- If the GPU crashes mid-file (bad driver), that file is retried on the CPU automatically.

| Model | Download | Use for |
|---|---|---|
| tiny / base | 75 / 145 MB | instant drafts of quick memos |
| small | 480 MB | default on PCs without an NVIDIA GPU |
| medium | 1.5 GB | good accuracy on CPU, slower |
| **large-v3-turbo** | 1.6 GB | **default** — near large-v3 accuracy at ~6–8× the speed |
| large-v3 | 3.1 GB | hardest audio; also used automatically for **Translate to English** (turbo can't translate) |

Speed on an RTX 4090 with large-v3-turbo: a 1-hour recording took about 18 seconds. CPU-only is much slower (use small or base there).

## Command line

```bat
.venv\Scripts\python -m dltr --cli [--model large-v3] [--lang zh] [--translate] [--mixed] [--speakers | --speakers=N] [--formats pdf,txt,srt,vtt,json,csv] [--outdir DIR] FILE|FOLDER ...
```

Settings and log: `%LOCALAPPDATA%\dl_transcribe\`.

## Updates

**Check for updates** asks GitHub for the latest release of this repo. If its tag (e.g. `v1.1.0`) is newer than the running version, **Update all** downloads `dl_transcribe-windows.zip` from that release; a small helper swaps the files once the app closes and starts it again. Downloaded models and settings are kept. If you're already on the latest version it says so. It also checks downloaded models for newer versions on Hugging Face.

To publish a new version: raise `APP_VERSION` in `dltr/common.py`, build, then

```bat
gh release create v1.1.0 dist\Windows\dl_transcribe-windows.zip dist\MacOS\dl_transcribe-mac-apple-silicon.dmg --title "1.1.0" --notes "What changed"
```

## Build the Mac app

A Mac app has to be built on a Mac. GitHub Actions does it: **Actions → Build macOS app → Run workflow** (or `gh workflow run mac.yml`, or push to the `mac-build` branch). The workflow runs `build_mac.py` on an Apple Silicon Mac, which builds the `.app`, tests it (transcribes a two-voice test recording with speakers, converts and plays audio, writes a PDF in several scripts, opens the window, checks for updates), packs `dl_transcribe-mac-apple-silicon.dmg` and runs the real updater against it. Download the .dmg into `dist/MacOS`:

```bat
gh run download -n dl_transcribe-mac-apple-silicon -D dist\MacOS
```

## Licenses

Whisper models (MIT, OpenAI) via faster-whisper / CTranslate2 (MIT). Speaker detection: sherpa-onnx (Apache-2.0), pyannote segmentation 3.0 (MIT), 3D-Speaker ERes2Net (Apache-2.0).
