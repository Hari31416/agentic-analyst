# Phase 07: language capabilities and voice

Prerequisite: phase 06. Hindi/English text metadata and baseline retrieval already work.
Read the language library's actual provider protocols, clients, and capability tests.
Outcome: extensible language handling and usable voice where providers are available.

## Build in order

1. Consolidate language capabilities into an app adapter using the library's
   registry. Track canonical language tags, script, mixed segments, user answer
   preference, uncertainty, provider/model, and capability availability. Adding a
   language must use registry/assets/configuration rather than branches in the agent.
2. Improve Hindi/English and Romanized-Hindi retrieval with measured query
   variants, transliteration, domain aliases/glossaries, and protected terms.
   Prefer original-language evidence and direct multilingual answers. Normalize
   search forms without overwriting original text or changing identifier values.
3. Add optional translation through provider interfaces. Protect citations,
   numbers, names, units, URLs, code, and SQL identifiers. Store translated text
   as derived content with original references. Select a validated local adapter
   when required; remote providers are available only through explicit policy.
4. Integrate local Faster-Whisper STT through `indic-language-utils`, with explicit
   model assets, audio decoding, duration/size limits, and device/resource settings.
   Build push-to-talk upload, editable transcript, language preference, and an
   explicit send action. Transcription alone must not execute database/analysis tools.
5. Integrate optional TTS through the same capability interfaces. Evaluate a
   suitable Hindi/English local model and add an app-owned provider adapter if
   the inspected library lacks one. Allow configured remote providers only where
   approved. Unsupported or unconfigured TTS reports unavailable, not dummy audio.
6. Add reviewed English/Hindi UI catalogs for user-visible status, source errors,
   connection/upload forms, voice controls, and evaluation labels. Keep technical
   source names and code intact. Confirm chosen fonts render Devanagari and mixed text.
7. Expose a small capability/status API and clear UI availability. Default audio
   handling is transient; persist audio only when explicitly enabled. Bound queues,
   cleanup temporary media, cache by provider/model/language/policy version, and
   prevent fallback routes from bypassing local-data policy.
8. Expand test cases for code-switching, transliteration ambiguity, Hindi numbers,
   dates and proper names, mixed-script identifiers, and source citations through
   translation. Include actual Hindi/English recorded or generated test audio
   with known transcripts and clearly recorded fixture provenance.

## Acceptance and validation

- [ ] Hindi/English document, SQL, and Python questions preserve numbers, names,
  identifiers, citations, and requested answer language.
- [ ] Romanized-Hindi and cross-language retrieval improvements are measured
  against the earlier baseline, including false matches and unresolved ambiguity.
- [ ] Actual STT audio reaches an editable transcript and executes only after send.
- [ ] Available TTS produces playable audio and stop/cleanup works; unavailable
  capabilities are clearly represented and cannot be advertised as working.
- [ ] A new language can be registered without changing the main loop.
- [ ] Invalid/oversized audio, provider failure, denied fallback, and mixed-language
  uncertainty have useful errors and do not lose the user's original text.
- [ ] External-provider policy and temporary-audio cleanup are tested.
- [ ] Critical Hindi UI text and audio numerals/names have a recorded review status.

The feature gate is explicit per capability: core Hindi/English text and local
STT must be exercised; translation/TTS may remain unavailable if no viable
configured provider exists. Record that limitation and its required assets.
Do not mark unavailable optional providers as implemented inference.

## Handoff

Record the capability matrix, actual tested providers/models/assets, resource use,
language coverage, review status, privacy settings, and unsupported speech cases.
