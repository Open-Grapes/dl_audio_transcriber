"""What changed in each version, newest first. Shown by Check for updates → Version history.
Add a section here with every new version."""

HISTORY = """
## 2.0  (21 Nov 2026)
- **Fixed**: running a file a second time could stop at 5 % and not respond to Stop. Re-runs work every time now
- **⟳ Force refresh** at the top: if anything ever seems stuck, it stops everything, starts the background workers afresh and puts the file back in the list, ready for Start
- **Faster**: switching tabs, light / dark and dragging dividers are much quicker in Fresh Mint, and resizing the window is smooth (it no longer redraws everything at every step)
- **Updates**: each time the app opens it checks quietly for a new version. Only when there is one does it ask: **Update now** or **Later**
- **Transcribe** in Fresh Mint: a slim drop strip and one wide settings card, so the transcript gets more room
- Drag the divider above the transcript all the way up for a big transcript; it's back in its usual place next time the app opens
- **⤢ Bigger window** for the transcript (the button in the top-right corner of the transcript), with the player (play, position, volume)
- **English** under each line of the transcript (next to Pinyin), translated offline, also on the Translate tab. Greyed out for English recordings
- A little more space between sentences (also in the Translate boxes); click empty space in the text to clear the highlight
- **Pinyin** only for Chinese (simplified, traditional, Cantonese); greyed out for other languages
- **Chat**: Pinyin and English switches for the questions, answers and suggestions; the buttons always stay in view and a long file name wraps
- **Summary**: **Import from Transcribe** and **Import from Translate** buttons; ask the AI about the text (e.g. “What should we do next?”): type it under the summary and press Enter or ➤
- **Translate**: From starts on Auto-detect, and follows the language of an imported recording
- **Speakers**: How many can now be set to 1
- The bigger transcript window closes with its own X (or Esc); round option dots have a little space before their words
- **Mac**: all of the above on the Mac too (the window-resizing speed-up is a Windows fix)
- Vocabulary box removed (it wasn't needed)

## 1.9  (20 Nov 2026)
- **Fresh Mint, redesigned**: rounded cards, pill buttons, on/off switches, format chips, Lucide line icons and the Nunito font, like the Fresh Mint design
- **Transcribe** in Fresh Mint: a big drop area next to the settings card, the file list, the transcript card with its player, and a Start card at the bottom
- Translate, Summary and Chat sit on a rounded card; the player has a round play button and slider knobs
- Legacy keeps the original look, in light and dark
- **Mac**: Fresh Mint on the Mac too, with the same Nunito font and line icons (Lucide, included with the app)

## 1.8  (20 Nov 2026)
- New look: **Fresh Mint** (light or dark), with the tabs and tools in a menu on the left. It's the new default
- **Theme…**: choose Fresh Mint or **Legacy** (the look up to 1.7), then Apply. Restart now switches it straight away; your choice is remembered
- The divider between two boxes has a clear grip (light in dark mode). Drag it to make either box bigger
- **Summary**: the AI summary is on top and the text it works from is below, with the divider right under the summary
- **Chat**: drag the divider above the question box. Drop a document (TXT, PDF, Word …) or a recording on the Chat tab to chat about it
- **Chat**: the file you're chatting about is shown clearly at the top (📄 document or 🎧 recording)
- Right-click menu: one **Copy** (copies what you highlighted, or the sentence you right-clicked)
- New **Version history** in Check for updates: what changed in every version
- Fix: a failed export showed no error message

## 1.6  (20 Nov 2026)
- **🔊 Read aloud** on the Translate tab: offline voices that are native to the language (Mandarin, English, Malay, ~40 Piper languages, ~1,100 more with MMS). Choose and preview the voice you like
- **Translate**: type straight in, paste (broken lines are joined), or **Open file…** (text, PDF, Word, SRT, VTT, or audio / video). From follows the recording's language
- **Pinyin** under Chinese lines
- **Transcribe**: Mandarin gets punctuation and sentences; new lines at pauses; one sentence per timestamp
- **Chat**: 📎 Add file… to chat about a document or a recording; a hint in the empty question box
- **■ Stop all** while anything runs; Summary text box you can edit; right-click a status message to copy it
- Fix: files in very deep folders couldn't be played

## 1.5  (18 Nov 2026)
- **Chat**: Mic ▾ to choose the microphone; suggestions come in the language you type in
- Speakers read **♀ Female / ♂ Male**; names start with a capital letter
- The window title shows the version
- Fix: "AI" jumped up next to your question while an answer was written

## 1.4  (18 Nov 2026)
- New **Chat** tab: ask the offline AI anything about a recording, with clickable [m:ss] times, 3 suggested follow-up questions and 🎤 Speak
- Speakers marked female / male voice, named automatically when the recording says their names, and a Speakers… window to rename them
- New Check for updates window with release notes, a progress bar and automatic daily checks

## 1.3  (18 Nov 2026)
- New **Share via Wi-Fi**: phones and other computers on the same Wi-Fi use this computer from their browser (QR code), with an activity log, Block device and upload clean-up
- Models button in the top bar; Find (Ctrl+F) in Translate and Summary; language lists filter as you type
- Player bar on the Translate tab; summaries export as PDF

## 1.2  (17 Nov 2026)
- New **Translate** tab: 200 languages offline (AI Qwen3 or NLLB-200), sentence by sentence with times and speakers, export PDF / TXT / SRT / VTT / JSON / CSV
- New **Summary** tab: overview, key points, key moments and action items in the language you pick
- Chinese / Japanese text no longer looks bold on Windows

## 1.1  (17 Nov 2026)
- New **Mac version** (Apple Silicon, macOS 14+)
- Clearer message when GitHub limits update checks

## 1.0  (17 Nov 2026)
- First release: offline transcription with Whisper on an NVIDIA GPU or the processor, 99 languages, mixed languages
- Speakers (who said what), built-in player (click a sentence to play it), edit in place, Find
- Export PDF, TXT, SRT, VTT, JSON, CSV and MP3; Models window; Check for updates
"""
