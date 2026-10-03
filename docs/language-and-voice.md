# Language and voice baseline

Phase 07 uses the pinned `indic-language-utils` revision
`39921fa66670b2da40c8638b701f29895467e4a2`. Registry canonicalization and local
speech inference use its real `DEFAULT_LANGUAGE_REGISTRY`, language tags,
`FasterWhisperSTTConfig`, and `FasterWhisperSTTProvider`. Script detection,
Romanized-Hindi heuristics, retrieval glossary and translation-literal protection
are small app-owned adapters. This is a first working baseline, not a claim of
complete language coverage or accurate unattended transcription.

| Capability | Baseline | Provider/assets |
| --- | --- | --- |
| Text metadata | Available, best effort | Library registry plus script/word heuristics; uncertainty labels are not calibrated probabilities |
| Direct Hindi/English answers | Exercised | Configured chat endpoint; prompt `analyst-v5` carries explicit answer preference |
| Retrieval variants | Optional Advanced profile | Deployment-owned `app/language/glossary.json`; original query always retained |
| Local STT | Exercised with Hindi/English synthetic audio | Library Faster-Whisper provider, CPU int8, two threads, one worker, beam1 |
| Translation | Unavailable | No viable configured local provider; remote library providers not enabled |
| TTS/read-aloud | Unavailable | No configured portable provider; no dummy audio |
| Hindi UI | Core controls available | Small reviewed catalogs, bundled OFL Noto Sans Devanagari |

## Setup and API

Run `make speech-model` to provision public
[Systran multilingual tiny assets](https://huggingface.co/Systran/faster-whisper-tiny),
pinned to `d90ca5fe260221311c53c58e660288d3deb8d356`, with file SHA-256 hashes
in `analyst-speech-model.json`. The model card lists an MIT license. The setup
command downloads assets and selects the ignored local `SPEECH_MODEL_PATH`;
request handling never downloads model/tokenizer files. Missing, changed or
invalid assets make speech unavailable. Restart the API after changing assets.

`GET /api/languages/capabilities` lists configured canonical tags and capability
availability/reasons. `POST /api/languages/transcribe` takes multipart `audio`
and optional `language`. It returns the draft text, language, timed segments,
provider/model, duration and an explicit verification warning. The endpoint
creates no runs, jobs, messages or artifacts. Use the existing Send action after
editing the draft. The requested answer language and UI language are independent.
The UI releases microphone tracks, aborts pending requests and rejects stale
transcripts when the thread changes. Existing typed text is preserved.

Audio is transient. Upload spooling is closed after reading, decoding uses an
in-memory buffer, and containers are closed even on failures. No raw audio is
retained as an application artifact or cached. Explicit audio retention is not
implemented in this baseline. Defaults are 10 MiB, 60 seconds, one active and one
waiting request; extra requests receive 429. Accept mono/stereo audio at
8–96 kHz and normalize it to mono 16 kHz PCM. Malformed/empty/unsupported audio
returns 422, oversize/long audio 413, unavailable STT 503, provider failure 502,
and timeout 504. Whisper's native worker cannot be safely killed: after timeout
or cancelled provider work, admission stays closed until API restart rather than
accumulating background requests. The limits have hard configuration ceilings.

The inspected [Faster-Whisper implementation](https://github.com/SYSTRAN/faster-whisper)
decodes with PyAV. Faster-Whisper 1.2.1 failed with newly resolved PyAV19 because
`metadata_errors` was rejected. PyAV16.1.0 is pinned in the lockfile and exercised.
This was a dependency failure, not an ASR-quality failure.

## Text, retrieval and extensions

Messages keep their original text and store language/script/mixed segments,
uncertainty, requested preference and provider in metadata. Source language
metadata remains compatible. Script detection cannot reliably distinguish all
languages sharing a script. Romanized-Hindi recognition uses a small vocabulary;
short or unfamiliar text stays uncertain. Names, identifiers and original
numerals are not overwritten. The prompt asks for direct multilingual answers,
with code, SQL identifiers, amounts and citations kept exact.

Additional queries supply glossary Hindi/English/Romanized terms and ASCII
search forms of standalone Devanagari numerals. Quoted/code/URL/identifier spans
are excluded from glossary matching. Hints do not resolve entity mappings or
ambiguous words such as `kal`. Basic retrieval stays unchanged. The measured
four-document fixture showed no recall improvement: English and Hindi recall@3
remained 0.50; Romanized-Hindi recall@3 fell from 0.50 Basic to 0.25 Advanced, with
non-supporting top-three passages rising from 1 to 2. The Advanced comparison also
includes keyword/fusion stages, so it is not a glossary-only ablation. All reached
recall@10=1.00 for this small corpus. These misses are retained, not tuned away.

Add an existing library-registry language tag to `SUPPORTED_LANGUAGES` as JSON.
The Bengali contract test exercises this without a new main-loop branch.
Speech availability lists the library provider’s declared supported tags; unsupported
language requests reject before decoding. Speech coverage remains provider-specific; an unsupported speech language must
not be interpreted as a new STT model. Custom languages require registry/assets
work, and text detection remains best effort. No translation is performed here;
its preparatory protection helper restores likely literals only when every token
survives exactly once. It does not certify arbitrary proper-name recognition.
A future translation provider must preserve originals and derived references,
apply external-data policy on every route, and version any content cache.

## Retained verification

`evals/fixtures/voice-v1/manifest.json` records expected transcripts, voice names,
durations, hashes and provenance. Audio is synthetic macOS `say` output using
Rishi/Lekha at 145 words/minute, not client recordings or an app TTS provider.
English retains 25000 and 001 but mistranscribes the scheme. Hindi returns Romanized
text with false 5K and garbled identifiers. Both reach editable drafts; they need
user correction. Native-speaker review and full browser QA remain deferred.

Eight local retrieval cases, two local STT cases and resource measurements are
in `evals/reports/phase07-20261003T074004.json`; the initial decoder failure is
retained separately. Combined retrieval/STT process peak RSS was recorded, not
used as a deployment capacity estimate. Three configured-model runs completed
English file SQL, Hindi Python and Hindi document questions without quality
retries. Results and HTTP checks are in
`evals/reports/phase07-agent-and-http-2026-10-03.json`. They preserve numeric totals
and requested answer language; formatting and omitted-qualifier limits are noted.
The audio HTTP check verifies unchanged run/job/message/artifact counts.

`make live-language` refreshes local measurements into a timestamped report.
Reuse saved results for routine edits. Improve coverage or model quality in a
later requested phase rather than treating perfect language output as a gate.
