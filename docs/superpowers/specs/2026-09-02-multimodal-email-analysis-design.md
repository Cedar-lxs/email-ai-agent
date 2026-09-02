# Multimodal Email Analysis Design

## Goal

Add multimodal analysis for after-sales emails so the agent can use customer-provided images and videos together with the email subject/body, local knowledge retrieval, and existing safety rules.

The first implementation must support:

- Image attachments.
- Video attachments through extracted key frames.
- HTML inline images referenced by `cid:`.
- HTML inline base64 images using `data:image/...;base64,...`.
- Multimodal results in intent judgment, RAG query construction, reply generation, review UI, and stored audit data.

The first implementation will not download public remote image URLs from HTML. It may record those URLs as unprocessed media references for human review.

## Current System Context

The current project has a clear layered design:

- `MailFetcher` parses unread IMAP messages into `ParsedEmail`.
- `EmailAgent` orchestrates intent analysis, retrieval, reply generation, draft saving, and sending.
- `AIProcessor` owns text-only LLM prompts and API calls.
- `KnowledgeBase` and retrievers load local knowledge and return evidence.
- `EmailDB` stores mail state, original body, draft text, and retrieval traces.
- Flask API routes expose review, knowledge, and settings data to the Vue frontend.

Multimodal support should extend these existing boundaries rather than moving parsing, model calls, or UI concerns into the main orchestration class.

## Proposed Architecture

Add a new media and multimodal path:

1. `MailFetcher` extracts media parts while parsing the message.
2. Extracted media is represented in domain models as metadata plus bytes.
3. A new media storage component writes accepted media to `data/media/<message-safe-id>/`.
4. A new `MultimodalAnalyzer` calls the DeepSeek vision model for images.
5. Video files are converted to a limited number of key-frame images before analysis.
6. `EmailAgent` combines text intent, multimodal observations, and knowledge retrieval.
7. `EmailDB` stores the multimodal result and media manifest for review and audit.
8. The Web API exposes multimodal summaries and safe local media preview endpoints.

## Domain Model Changes

Add these dataclasses in `domain/models.py`:

- `EmailMedia`
  - `media_id`
  - `filename`
  - `content_type`
  - `source`
  - `content_id`
  - `size_bytes`
  - `data`

- `StoredMedia`
  - `media_id`
  - `filename`
  - `content_type`
  - `source`
  - `path`
  - `size_bytes`
  - `derived_from`
  - `metadata`

- `MultimodalObservation`
  - `summary`
  - `visible_text`
  - `product_identifiers`
  - `fault_signals`
  - `connection_state`
  - `indicator_state`
  - `risk_signals`
  - `needed_information`
  - `confidence`
  - `raw_items`
  - `errors`

Extend `ParsedEmail` with:

- `media: list[EmailMedia]`

This keeps attachments and inline media close to the parsed email while keeping storage and model calls outside `MailFetcher`.

## Mail Parsing

`MailFetcher._parse_raw_email` will inspect each MIME part:

- Text/plain and text/html continue to populate `body_text` and `body_html`.
- Attachments whose content type starts with `image/` or `video/` are collected.
- Inline MIME parts with a `Content-ID` and image content type are collected as source `inline_cid`.
- Data URL images embedded in HTML are extracted as source `inline_data`.
- Remote HTML images such as `https://...` are not downloaded in v1.

Supported v1 image content types:

- `image/jpeg`
- `image/png`
- `image/gif`
- `image/webp`

Supported v1 video content types:

- `video/mp4`
- `video/quicktime`
- `video/webm`
- `video/x-msvideo`

Unsupported media is ignored with a parse note if the structure later needs it.

## Media Storage

Add `infrastructure/media_storage.py`.

Responsibilities:

- Create `data/media/<message-safe-id>/`.
- Sanitize filenames.
- Enforce configured limits.
- Write original supported images and videos.
- Write extracted video frames under the same message directory.
- Return a manifest of `StoredMedia` entries.

Recommended config:

```yaml
multimodal:
  enabled: true
  provider: "deepseek"
  api_base: "https://api.deepseek.com"
  model: "deepseek-v4-flash-vision-exp"
  max_media_per_email: 8
  max_image_bytes: 10485760
  max_video_bytes: 52428800
  video_frame_count: 3
  analysis_timeout: 90
```

The API key should reuse `AI_API_KEY` unless `MULTIMODAL_API_KEY` is set.

## Video Frame Extraction

Prefer OpenCV if installed. If OpenCV is unavailable, v1 should safely skip video frame extraction and record a multimodal error. The project should not require FFmpeg or GUI tools for the first version.

Frame selection:

- Read video frame count when possible.
- Extract up to `video_frame_count` frames across the duration.
- Store frames as JPEG.
- If the video cannot be decoded, record the error and continue processing any other media.

Video decode failures should not fail the whole email. They should reduce confidence and remain visible to the reviewer.

## DeepSeek Multimodal Analyzer

Add `infrastructure/multimodal.py`.

The analyzer will:

- Accept subject, body text, and stored image/frame files.
- Convert image bytes to base64 data URLs.
- Send messages to DeepSeek using OpenAI-compatible multimodal content blocks.
- Ask for strict JSON output.
- Repair common JSON formatting issues using the existing parsing helper pattern.
- Return `MultimodalObservation`.

Prompt output should include:

- Overall visible issue summary.
- Any visible model numbers, labels, ports, LED states, error messages, or screen text.
- Whether the media appears to show connection, power, network, physical damage, water damage, smoke, burnt marks, exposed wiring, disassembly, or safety risk.
- Whether the image/video is too blurry or incomplete.
- Which extra information is required before giving product-specific guidance.
- A confidence score from 0 to 1.

The analyzer must not let image content override product knowledge. Visual observations are evidence for diagnosis and retrieval, not permission to invent specifications or steps.

## Email Processing Flow

`EmailAgent._process_email_async` should change from:

1. Text intent.
2. Text translation.
3. Retrieval.
4. Reply.

To:

1. Text intent.
2. Store media and run multimodal analysis when enabled.
3. Re-evaluate human escalation using text intent plus multimodal risk signals.
4. Translate text for retrieval.
5. Build retrieval query with translated text plus multimodal identifiers and fault signals.
6. Retrieve local knowledge.
7. Generate reply with knowledge, conversation history, and multimodal observation.
8. Save multimodal trace beside retrieval trace.

If multimodal analysis fails but text flow is otherwise valid, continue in semi-auto mode and include a note for reviewer. In full-auto mode, a multimodal failure should block auto-send when media exists, because the attached context may be important.

## Safety Rules

The following multimodal risk signals require human review:

- Smoke, burnt marks, melted housing, fire signs.
- Water damage, liquid inside equipment, corrosion.
- Exposed wiring, unsafe power cabling, damaged power adapter.
- Disassembly, open casing, PCB handling.
- Requests or images suggesting firmware flashing, factory reset, data deletion, voltage changes, or PoE power modification.
- Strong mismatch between customer text and media observation.
- Low confidence when media is central to the question.

The reply generator may refer to visual observations conservatively:

- Good: "The image appears to show that the port indicator is off."
- Bad: "The switch is definitely defective."

## Database Changes

Extend `processed_emails` with:

- `media_manifest TEXT DEFAULT ''`
- `multimodal_trace TEXT DEFAULT ''`

Both fields store JSON.

Add helper methods:

- `save_media_manifest(message_id, manifest)`
- `save_multimodal_trace(message_id, trace)`
- `parse_media_manifest(row)`
- `parse_multimodal_trace(row)`

Existing migrations are additive, matching the current database style.

## Web API and UI

API additions:

- `GET /api/mails/<message_id>` includes `media` and `multimodal`.
- `GET /api/mails/<message_id>/media/<media_id>` serves stored media files from the safe message directory.

UI changes:

- Mail detail shows a multimodal analysis panel.
- The panel lists summary, visible text, model identifiers, fault/risk signals, confidence, and errors.
- Images and extracted video frames are shown as previews.
- Original videos can be listed by filename and size. Playback can be added only if serving the original file is safe and useful.

## Configuration and Defaults

Default behavior should be conservative:

- `multimodal.enabled` should default to `false` unless explicitly configured.
- If enabled and media exists, failures do not block draft generation in `semi_auto`.
- If enabled and media exists, failures prevent `full_auto` sending.
- Media size limits must be enforced before writing large files or calling the model.

## Testing Strategy

Add unit tests for:

- Image attachment extraction.
- `cid:` inline image extraction.
- HTML base64 data image extraction.
- Video attachment manifest handling.
- Media storage filename sanitization and size limits.
- Multimodal analyzer request formatting with mocked HTTP calls.
- JSON parsing and fallback on malformed model output.
- Retrieval query enrichment from visual identifiers and fault signals.
- Human escalation on visual risk signals.
- Web API returning multimodal trace and media manifest.

Update existing tests where `ParsedEmail` construction needs the new `media` field.

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src main.py web_app.py tests
```

## Rollout Notes

The first release should document that:

- DeepSeek vision is used for images.
- Video support is implemented through extracted still frames.
- Public remote HTML image URLs are not fetched.
- Multimodal observations are assistive and should be reviewed by a human before relying on them for risky diagnostics.

