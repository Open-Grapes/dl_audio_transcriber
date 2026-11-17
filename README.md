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
- **Translate tab**: translate a transcript (or any pasted text) into any of **200 languages**, offline. Every sentence keeps its timestamp and speaker, the original and the translation are shown one above the other, and clicking a sentence highlights its match in both (and plays it). Export the translation as PDF / TXT / SRT / VTT / JSON / CSV.
- **Share via Wi-Fi**: phones and other computers on your Wi-Fi open a link (or scan a QR code) and upload, play, transcribe, translate, summarise and download in their browser, using this PC.
- **Summary tab**: an offline **AI summary** of a transcript, translation or pasted text: overview, key points, key moments with their times and action items, in the language you pick.
- **Mixed languages** (Transcribe tab): tick this for recordings that switch language (e.g. English ↔ Mandarin); language is detected per section instead of once.
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

### Share via Wi-Fi (phones, tablets, other laptops)

Click **Share via Wi-Fi** (top right). A window shows a QR code and two addresses, such as `http://192.168.0.106` and `http://dl-transcribe.local`. On a phone or another computer **on the same Wi-Fi**, scan the code with the camera or type an address into the browser. In that page they can:

- **upload** audio or video (voice memos, recordings, videos), pick the language / model / Mixed languages / Speakers, and press **Start**;
- see progress (several people can queue recordings; they're done one after another);
- **play** the recording and tap any sentence to hear it, with the highlight following the audio;
- **translate** it into any of 200 languages (original and translation shown sentence by sentence) and make an **AI summary**;
- **download** PDF / TXT / SRT / VTT / JSON / CSV of the transcript or the translation.

This PC does all the work (with its GPU), so it must stay on with the app open; nothing goes to the internet. Sharing stays on until you click **Turn off** or close the app, and uploads are deleted then. There's no password, so anyone on the same Wi-Fi who knows the address can use it while sharing is on.

**Phone can't connect?** The first time, Windows asks whether to allow dl_transcribe on networks: allow it on *Private networks*. Your Wi-Fi must also be set to *Private* in Windows Settings → Network & internet. Some networks (offices, hotels) block devices from seeing each other, and some block `.local` names; then use the number address.

## Translate

1. On the **Transcribe** tab, click the transcript you want. On the **Translate** tab press **Import from Transcribe** (or **Paste text** to translate anything, e.g. text copied from a website or a chat).
2. **From**: *Auto-detect* (it works sentence by sentence, so recordings that mix English, Chinese, Japanese … are fine) or pick the language if it guesses wrong. **To**: any of 200 languages. Type in either list to search it.
3. **Translator**:
   - *Auto* (default) uses the AI model (Qwen3) when both languages are ones it handles well (English, Chinese, Cantonese, Japanese, Korean, Malay, Indonesian, Thai, Vietnamese, Tagalog, the main European languages …) and the PC has an NVIDIA GPU; otherwise NLLB-200.
   - *AI: Qwen3*: most natural wording, uses the sentences around each one for context.
   - *NLLB-200*: Meta's translator for 200 languages (best for Tamil and other less common languages), quick even without a GPU.
4. Press **Translate** (nothing runs before that; it turns into **Stop** while working). The first time it offers to download the model it needs (one time).
5. Click any sentence in either box: both highlight the pair, and for a transcript it plays from there (**Play / Pause** too); the highlight follows the audio. **Export** saves the translation with the formats and folder chosen on the Transcribe tab, as `name_zh-Hans.pdf`, `name_ja.srt` …

## Summary

Pick what to summarise (the **Transcript** selected on the Transcribe tab, the **Translation**, or **Pasted text**), the language to **write in** and the **length**, then press **Summarise**. You get an overview, key points, key moments with their `(m:ss)` times and action items (when there are any). Long recordings are read part by part, then summarised as a whole. **Copy** or **Save as…** (.txt or .md). It's made by an AI model on your PC, so check important details against the transcript.

On an RTX 4090 a summary of a few minutes of speech takes a few seconds and translation about 3 sentences a second; without an NVIDIA GPU (and on a Mac) expect minutes for a long recording.

## Models

None are included in the download (the translation and AI models download the first time you use the Translate or Summary tab). The first time you press Start with a model that isn't on the PC yet, the app asks: **Download <model>** (just that one) or **Download all** (every model, ~6.9 GB, so you can switch later without internet). Models are saved to `%LOCALAPPDATA%\dl_transcribe\models`. After that, everything runs offline.

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
| large-v3 | 3.1 GB | hardest audio, rare languages, Cantonese |
| Qwen3-4B AI | 4.0 GB | Summary tab + natural translation (Apache-2.0) |
| NLLB-200 3.3B / 1.3B | 3.4 / 1.4 GB | Translate tab, 200 languages (free for non-commercial use, CC-BY-NC-4.0) |

Speed on an RTX 4090 with large-v3-turbo: a 1-hour recording took about 18 seconds. CPU-only is much slower (use small or base there).

## Command line

```bat
.venv\Scripts\python -m dltr --cli [--model large-v3] [--lang zh] [--translate] [--mixed] [--speakers | --speakers=N] [--formats pdf,txt,srt,vtt,json,csv] [--outdir DIR] FILE|FOLDER ...
```

Settings and log: `%LOCALAPPDATA%\dl_transcribe\`.

## Updates

**Check for updates** asks GitHub for the latest release of this repo. If its tag (e.g. `v1.3`) is newer than the running version, **Update all** downloads `dl_transcribe-windows.zip` from that release; a small helper swaps the files once the app closes and starts it again. Downloaded models and settings are kept. If you're already on the latest version it says so. It also checks downloaded models for newer versions on Hugging Face.

To publish a new version: raise `APP_VERSION` in `dltr/common.py`, build, then

```bat
gh release create v1.3 dist\Windows\dl_transcribe-windows.zip dist\MacOS\dl_transcribe-mac-apple-silicon.dmg --title "1.3" --notes "What changed"
```

## Build the Mac app

A Mac app has to be built on a Mac. GitHub Actions does it: **Actions → Build macOS app → Run workflow** (or `gh workflow run mac.yml`, or push to the `mac-build` branch). The workflow runs `build_mac.py` on an Apple Silicon Mac, which builds the `.app`, tests it (transcribes a two-voice test recording with speakers, converts and plays audio, writes a PDF in several scripts, opens the window, checks for updates), packs `dl_transcribe-mac-apple-silicon.dmg` and runs the real updater against it. Download the .dmg into `dist/MacOS`:

```bat
gh run download -n dl_transcribe-mac-apple-silicon -D dist\MacOS
```

## Licenses

Whisper models (MIT, OpenAI) via faster-whisper / CTranslate2 (MIT). Translation: NLLB-200 (Meta, CC-BY-NC-4.0, non-commercial use) and Qwen3-4B-Instruct-2507 (Alibaba, Apache-2.0), CTranslate2 conversions by OpenNMT and jncraton. Language detection: langid.py (BSD). Speaker detection: sherpa-onnx (Apache-2.0), pyannote segmentation 3.0 (MIT), 3D-Speaker ERes2Net (Apache-2.0).
