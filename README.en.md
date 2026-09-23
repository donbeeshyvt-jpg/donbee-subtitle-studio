# DonBee Subtitle Studio

[繁體中文](README.md) · [Setup guide (Traditional Chinese)](guides/SETUP.md) · [API / CLI guide](guides/API_CLI.md)

Downloading a clip, finding the right moments and adding subtitles should not require hopping between several tools.

DonBee Subtitle Studio puts those steps in one local browser workspace. Paste a YouTube link, choose time ranges and an output folder, then transcribe, correct, edit and export. You can also import your own video or audio and start with subtitles.

This is the first public code snapshot, **v2.0.0-preview.1**. “v2” identifies the web workbench; “preview” means it is not yet validated for every use case.

## What can you do with it?

- Download selected YouTube ranges as video or audio and save them to a chosen folder.
- Generate SRT subtitles locally with Breeze-ASR-25, Whisper large-v3 or Qwen3-ASR-1.7B.
- Use your own text AI to suggest contextual corrections: LM Studio / llama.cpp locally, or OpenRouter / DeepSeek online. Supply names, terminology and reference text, and retain revision history.
- Click subtitles to seek, adjust and reorder timeline segments, export separate clips or a merged file, and request AI summaries and highlights.

ElevenLabs and OpenRouter transcription are also available with your own credentials, supported models and explicit audio-upload consent. The current remote transcription pipeline still includes a local draft pass; it is not a local-model-free mode.

## A simple workflow

1. Create a project. Paste a YouTube URL on the download/edit page, or import a local file.
2. Enter a range such as `05:00–10:00`, select an output folder and download. Saved files go into a `subtitle_studio` subfolder.
3. Open the subtitles/correction page and select an installed ASR model. Add terminology hints for names or unfamiliar words.
4. Choose your text model. The web interface directly applies accepted corrections; review the before/after comparison and revert the batch if needed. Use the CLI for suggestions without applying changes. A high-confidence label is not a guarantee of correctness.
5. Preview subtitles, choose one or two sentences per cue and whether to keep punctuation, then export SRT. Export video clips from the editing page when needed.

Overlapping download ranges are merged: 5–10 and 9–15 minutes become one 5–15-minute acquisition. Create two timeline segments if you need two deliverables. Accurate cuts require re-encoding; stream-copy cuts have keyframe limitations.

Original media is preserved. Media exports avoid name collisions; subtitle and text sidecars update the same-named latest files. Use separate output folders or backups to retain distinct deliveries.

## Before you start

The primary environment is Windows with Python 3.12, FFmpeg (including ffprobe), and Node.js. The launcher checks requirements, shows installation locations and available download-size estimates, then asks for consent. The local option prepares WhisperX and Whisper large-v3 first, followed by draft, alignment and classification models under the project's `models/` directory. GPU compatibility and available memory still matter.

Setup also prepares an isolated yt-dlp environment. The CLI streams installation stages and model-download progress. After rechecking, it opens the browser only when the service health endpoint and workbench page are available. See the [setup guide](guides/SETUP.md) for commands and limitations. LM Studio is optional and must be running with a model loaded if selected.

Media, model weights, credentials and private development records are not included. Only download and process media you are authorized to use.

## Where to get the AI tools

- [OpenRouter](https://openrouter.ai/): sign up and obtain your own API key for online text models. Credits or fees depend on the selected model and service plan.
- [LM Studio](https://lmstudio.ai/): download the local application, load a model and start its local server. This is a local tool, not a required online transcription service.
- [ElevenLabs](https://elevenlabs.io/): sign up and create an API key with Speech to Text permissions for online transcription.

All are optional. Remote processing sends the relevant audio or text to the provider and may incur charges. Keep your keys private.

## Under the hood

React / TypeScript powers the interface, Python / FastAPI the backend, yt-dlp resolves download sources, and FFmpeg handles media. ASR integrates tools such as WhisperX / faster-whisper. Some timeline and segment controls are adapted from LosslessCut; this is not the complete LosslessCut application. See [third-party notices](THIRD_PARTY_NOTICES.md).

## One-click launch

Double-click **`Start-DonBee-Subtitle-Studio.bat`** in the project folder and confirm your setup choices. Once ready, it starts the local service, checks availability and opens your browser:

[Open DonBee Subtitle Studio](http://127.0.0.1:8765/v2/)

To have an AI operate it, ask the agent to read [AGENTS.md](AGENTS.md) and the [operator skill](.agents/skills/subtitle-studio/SKILL.md), then provide the URL, ranges, output folder and permission for any paid or remote processing. The CLI can run a saved workflow plan in one command; see the [API / CLI guide](guides/API_CLI.md).
