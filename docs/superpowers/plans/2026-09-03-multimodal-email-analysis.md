# Multimodal Email Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DeepSeek-based multimodal analysis for customer images and videos in after-sales email handling.

**Architecture:** Extend the existing layered Python app with media domain models, MIME media extraction, local media storage, a DeepSeek vision analyzer, and conservative orchestration in `EmailAgent`. Store media manifests and multimodal traces in SQLite, then expose them through the existing Flask API and Vue mail detail page.

**Tech Stack:** Python 3.10+, Flask, SQLite, httpx, unittest, Vue 3, Element Plus, optional OpenCV (`cv2`) for video frame extraction.

**Spec:** `docs/superpowers/specs/2026-09-02-multimodal-email-analysis-design.md`

## Global Constraints

- Support image attachments, video attachments through extracted key frames, `cid:` inline images, and HTML base64 inline images.
- Do not download public remote image URLs in v1.
- Use DeepSeek vision image input through OpenAI-compatible multimodal message content.
- Videos are analyzed only through locally extracted still frames.
- Multimodal failures do not block draft creation in `semi_auto`.
- Multimodal failures prevent direct send in `full_auto` when media exists.
- Visual risk signals such as smoke, burnt marks, water damage, exposed wiring, disassembly, unsafe power cabling, firmware flashing, factory reset, data deletion, voltage changes, or PoE power modification require human review.
- Default `multimodal.enabled` is conservative and must not surprise-send anything.
- Existing tests must continue to pass with `python -m unittest discover -s tests -v`.
- Syntax verification must pass with `python -m compileall -q src main.py web_app.py tests`.

---

## File Structure

- Modify `src/email_agent/domain/models.py`: add `EmailMedia`, `StoredMedia`, and `MultimodalObservation`; add `media` to `ParsedEmail`.
- Modify `src/email_agent/infrastructure/mail_fetcher.py`: extract supported attachments, `cid:` inline images, and base64 HTML images.
- Create `src/email_agent/infrastructure/media_storage.py`: save media safely, enforce limits, and optionally extract video frames.
- Create `src/email_agent/infrastructure/multimodal.py`: call DeepSeek vision and normalize JSON output.
- Modify `src/email_agent/application/email_service.py`: wire storage/analyzer into the existing mail flow, enrich retrieval, and apply risk escalation.
- Modify `src/email_agent/infrastructure/llm.py`: allow reply prompt to receive multimodal observations.
- Modify `src/email_agent/infrastructure/database.py`: add manifest/trace columns and JSON helpers.
- Modify `src/email_agent/bootstrap.py`: construct services with the new components through `EmailAgent`.
- Modify `src/email_agent/web/routes/api.py`: include media/multimodal data and serve local media safely.
- Modify `config.yaml` and `.env.example`: document DeepSeek multimodal config and optional API key override.
- Modify `frontend/src/views/MailDetail.vue`: show multimodal summary and previews from API data.
- Modify or add tests under `tests/`: cover parsing, storage, analyzer, orchestration, database, API, and frontend-facing response shape.

---

### Task 1: Domain Models and MIME Media Extraction

**Files:**
- Modify: `src/email_agent/domain/models.py`
- Modify: `src/email_agent/infrastructure/mail_fetcher.py`
- Test: `tests/test_multimodal_mail_fetcher.py`

**Interfaces:**
- Produces: `EmailMedia(media_id: str, filename: str, content_type: str, source: str, content_id: str = "", size_bytes: int = 0, data: bytes = b"", metadata: dict = field(default_factory=dict))`
- Produces: `ParsedEmail.media: list[EmailMedia]`
- Consumes: standard library `email` MIME parsing.

- [ ] **Step 1: Write failing tests for image attachments**

Add `tests/test_multimodal_mail_fetcher.py` with a MIME email containing a JPEG attachment. Assert that `_parse_raw_email()` returns one `EmailMedia` with `source == "attachment"`, `content_type == "image/jpeg"`, non-empty `data`, and a stable non-empty `media_id`.

```python
def test_extracts_image_attachment():
    raw = build_message_with_attachment(
        filename="fault.jpg",
        content_type="image/jpeg",
        payload=b"fake-jpeg",
    )
    parsed = MailFetcher("imap.example.com", 993, "a@example.com", "secret")._parse_raw_email(raw)
    assert len(parsed.media) == 1
    assert parsed.media[0].source == "attachment"
    assert parsed.media[0].filename == "fault.jpg"
    assert parsed.media[0].content_type == "image/jpeg"
    assert parsed.media[0].data == b"fake-jpeg"
```

- [ ] **Step 2: Write failing tests for inline `cid:` images**

Create a multipart/related message whose HTML body contains `<img src="cid:photo1">` and whose image part has `Content-ID: <photo1>`. Assert the image is captured as `inline_cid`.

```python
def test_extracts_cid_inline_image():
    raw = build_related_message_with_cid_image("photo1", b"inline-image")
    parsed = MailFetcher("imap.example.com", 993, "a@example.com", "secret")._parse_raw_email(raw)
    assert parsed.media[0].source == "inline_cid"
    assert parsed.media[0].content_id == "photo1"
```

- [ ] **Step 3: Write failing tests for HTML base64 images**

Use an HTML-only message with `<img src="data:image/png;base64,aW1hZ2U=">`. Assert it creates one `EmailMedia` with `source == "inline_data"` and decoded bytes `b"image"`.

```python
def test_extracts_base64_data_image_from_html():
    raw = build_html_message('<p>See this</p><img src="data:image/png;base64,aW1hZ2U=">')
    parsed = MailFetcher("imap.example.com", 993, "a@example.com", "secret")._parse_raw_email(raw)
    assert parsed.media[0].source == "inline_data"
    assert parsed.media[0].content_type == "image/png"
    assert parsed.media[0].data == b"image"
```

- [ ] **Step 4: Implement domain model additions**

Add the dataclasses to `domain/models.py`. Update `ParsedEmail` with `media: list[EmailMedia] = field(default_factory=list)` so existing callers keep working.

- [ ] **Step 5: Implement media extraction in `MailFetcher`**

Add class constants:

```python
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}
```

Collect media while walking MIME parts. Use `part.get_filename()` after MIME decoding, fallback to `inline-<content-id>.<ext>` or `media-<n>.<ext>`. Use SHA-256 of source, filename, content-id, and bytes to create `media_id`.

- [ ] **Step 6: Run task tests**

Run: `python -m unittest tests.test_multimodal_mail_fetcher -v`

- [ ] **Step 7: Commit**

```powershell
git add src/email_agent/domain/models.py src/email_agent/infrastructure/mail_fetcher.py tests/test_multimodal_mail_fetcher.py
git commit -m "feat: extract email media for multimodal analysis"
```

---

### Task 2: Media Storage and Video Key Frames

**Files:**
- Create: `src/email_agent/infrastructure/media_storage.py`
- Test: `tests/test_media_storage.py`

**Interfaces:**
- Consumes: `EmailMedia`
- Produces: `StoredMedia`
- Produces: `MediaStorage.save(message_id: str, media: list[EmailMedia]) -> tuple[list[StoredMedia], list[str]]`
- Produces: `MediaStorage.manifest(stored: list[StoredMedia]) -> list[dict]`

- [ ] **Step 1: Write failing tests for safe storage**

Create an `EmailMedia` named `"..\\bad:name.jpg"` and assert storage writes a sanitized filename under a temp media root, returns a `StoredMedia`, and never writes outside that root.

```python
def test_saves_media_with_sanitized_filename_inside_message_directory():
    media = EmailMedia("m1", "..\\bad:name.jpg", "image/jpeg", "attachment", size_bytes=4, data=b"data")
    storage = MediaStorage(temp_root, {"max_media_per_email": 8, "max_image_bytes": 100})
    stored, errors = storage.save("message/1@example.com", [media])
    assert errors == []
    assert len(stored) == 1
    assert Path(stored[0].path).is_file()
    assert temp_root.resolve() in Path(stored[0].path).resolve().parents
```

- [ ] **Step 2: Write failing tests for size and count limits**

Assert oversized images are skipped with an error and only `max_media_per_email` items are stored.

- [ ] **Step 3: Write failing tests for video frame fallback**

Pass a fake `video/mp4` when OpenCV is unavailable or cannot decode. Assert original video is stored, no frame is created, and a clear error is returned.

- [ ] **Step 4: Implement `MediaStorage`**

Implement:

```python
class MediaStorage:
    IMAGE_TYPES = {...}
    VIDEO_TYPES = {...}

    def __init__(self, media_root: Path, config: dict): ...
    def save(self, message_id: str, media: list[EmailMedia]) -> tuple[list[StoredMedia], list[str]]: ...
    def manifest(self, stored: list[StoredMedia]) -> list[dict]: ...
```

Use a safe message directory name from SHA-256 of `message_id`. Preserve readable sanitized filenames where possible.

- [ ] **Step 5: Implement optional OpenCV frame extraction**

Inside `_extract_video_frames`, import `cv2` inside the method. On import or decode failure, return `([], ["视频关键帧提取失败：..."])`. Do not add OpenCV to required dependencies in v1.

- [ ] **Step 6: Run task tests**

Run: `python -m unittest tests.test_media_storage -v`

- [ ] **Step 7: Commit**

```powershell
git add src/email_agent/infrastructure/media_storage.py tests/test_media_storage.py
git commit -m "feat: store email media for review"
```

---

### Task 3: DeepSeek Multimodal Analyzer

**Files:**
- Create: `src/email_agent/infrastructure/multimodal.py`
- Modify: `src/email_agent/infrastructure/llm.py`
- Test: `tests/test_multimodal_analyzer.py`

**Interfaces:**
- Consumes: `StoredMedia`
- Produces: `MultimodalAnalyzer.analyze(subject: str, body: str, media: list[StoredMedia]) -> MultimodalObservation`
- Produces: `MultimodalAnalyzer.analyze_async(subject: str, body: str, media: list[StoredMedia]) -> MultimodalObservation`
- Produces: `MultimodalObservation.to_context() -> str` or helper function `format_multimodal_context(observation) -> str`

- [ ] **Step 1: Write failing tests for request payload**

Mock `httpx.post` or `httpx.AsyncClient.post`. Assert the request goes to `{api_base}/v1/chat/completions`, uses the configured model, and includes a content list with one text block and one `image_url` block containing a data URL.

- [ ] **Step 2: Write failing tests for JSON normalization**

Mock a response JSON whose message content contains valid JSON. Assert `MultimodalObservation` fields are normalized into lists and `confidence` is clamped between `0.0` and `1.0`.

- [ ] **Step 3: Write failing tests for model/API failure**

Assert analyzer returns an observation with empty evidence fields and `errors` containing the failure message instead of raising for normal API failures.

- [ ] **Step 4: Implement analyzer**

Implement prompt and request builder. Use the same OpenAI-compatible style as `AIProcessor._call_openai`. Reuse `AIProcessor._parse_json_object` for robust JSON parsing.

- [ ] **Step 5: Implement context formatter**

Create a short plain-text context format:

```text
[Visual observation]
Summary: ...
Visible text: ...
Product identifiers: ...
Fault signals: ...
Risk signals: ...
Confidence: 0.82
```

- [ ] **Step 6: Run task tests**

Run: `python -m unittest tests.test_multimodal_analyzer -v`

- [ ] **Step 7: Commit**

```powershell
git add src/email_agent/infrastructure/multimodal.py src/email_agent/infrastructure/llm.py tests/test_multimodal_analyzer.py
git commit -m "feat: add deepseek multimodal analyzer"
```

---

### Task 4: Database Trace and Web API

**Files:**
- Modify: `src/email_agent/infrastructure/database.py`
- Modify: `src/email_agent/web/routes/api.py`
- Test: `tests/test_multimodal_api.py`

**Interfaces:**
- Produces: `EmailDB.save_media_manifest(message_id: str, manifest: list[dict])`
- Produces: `EmailDB.save_multimodal_trace(message_id: str, trace: dict)`
- Produces: `EmailDB.parse_media_manifest(row) -> list[dict]`
- Produces: `EmailDB.parse_multimodal_trace(row) -> dict`

- [ ] **Step 1: Write failing database tests**

Assert new columns are created on a fresh SQLite file and JSON helper methods round-trip manifest and multimodal trace data.

- [ ] **Step 2: Implement additive migrations**

Add `media_manifest` and `multimodal_trace` to the existing migration map in `EmailDB._init_tables`.

- [ ] **Step 3: Write failing API tests**

Use existing Flask testing pattern. Insert a processed email with saved manifest/trace. Assert `GET /api/mails/<message_id>` includes `media` and `multimodal`.

- [ ] **Step 4: Implement API response additions**

In `get_mail_detail`, add:

```python
"media": agent.db.parse_media_manifest(mail),
"multimodal": agent.db.parse_multimodal_trace(mail),
```

- [ ] **Step 5: Add safe media serving route**

Add `GET /api/mails/<message_id>/media/<media_id>`. Look up the manifest from DB, match by `media_id`, resolve the file path, and send only if it exists and is inside the configured media root.

- [ ] **Step 6: Run task tests**

Run: `python -m unittest tests.test_multimodal_api -v`

- [ ] **Step 7: Commit**

```powershell
git add src/email_agent/infrastructure/database.py src/email_agent/web/routes/api.py tests/test_multimodal_api.py
git commit -m "feat: expose multimodal review data"
```

---

### Task 5: Email Flow Integration and Safety Decisions

**Files:**
- Modify: `src/email_agent/application/email_service.py`
- Modify: `src/email_agent/infrastructure/llm.py`
- Modify: `src/email_agent/bootstrap.py`
- Modify: `config.yaml`
- Modify: `.env.example`
- Test: `tests/test_multimodal_email_service.py`

**Interfaces:**
- Consumes: `MediaStorage.save(...)`
- Consumes: `MultimodalAnalyzer.analyze_async(...)`
- Produces: `EmailAgent._multimodal_enabled() -> bool`
- Produces: `EmailAgent._visual_requires_human(observation: MultimodalObservation, media_count: int) -> bool`
- Produces: `EmailAgent._build_multimodal_retrieval_terms(observation: MultimodalObservation) -> list[str]`

- [ ] **Step 1: Write failing tests for semi-auto media analysis**

Build an `EmailAgent` with mocked DB, AI, retriever, sender, media storage, and analyzer. Process a parsed email with one image. Assert media manifest and multimodal trace are saved, retrieval query includes visual identifiers, and draft is saved.

- [ ] **Step 2: Write failing tests for risk escalation**

Analyzer returns `risk_signals=["burnt marks"]`. Assert email status becomes `escalated`, no reply is generated, and the mail is considered successfully handled.

- [ ] **Step 3: Write failing tests for full-auto failure blocking**

When mode is `full_auto`, media exists, and analyzer returns an error, assert `_can_auto_send` or its new wrapper returns false and the result becomes a draft or escalated record rather than sending.

- [ ] **Step 4: Wire components in `EmailAgent.__init__`**

Read `multimodal` config. Create `MediaStorage(paths.data / "media", config.get("multimodal", {}))` and `MultimodalAnalyzer(config)` when enabled.

- [ ] **Step 5: Integrate into async and sync processing paths**

Keep sync compatibility by adding sync wrappers or by sharing a private helper that accepts awaitable analyzer calls. Ensure both `_process_email_async` and `_process_email` preserve existing behavior when multimodal is disabled.

- [ ] **Step 6: Enrich retrieval and reply prompts**

Add visual text to `RetrievalQuery.summary` or `keywords`. Update `AIProcessor.generate_reply` and `generate_reply_async` signatures to accept `multimodal_context: str = ""`, then include it in `REPLY_PROMPT`.

- [ ] **Step 7: Update config defaults**

Add a disabled-by-default `multimodal` block in `config.yaml`, and add `MULTIMODAL_API_KEY=` to `.env.example`.

- [ ] **Step 8: Run integration tests**

Run: `python -m unittest tests.test_multimodal_email_service -v`

- [ ] **Step 9: Commit**

```powershell
git add src/email_agent/application/email_service.py src/email_agent/infrastructure/llm.py src/email_agent/bootstrap.py config.yaml .env.example tests/test_multimodal_email_service.py
git commit -m "feat: integrate multimodal email diagnosis"
```

---

### Task 6: Frontend Review Display

**Files:**
- Modify: `frontend/src/views/MailDetail.vue`
- Modify: `frontend/src/api/mail.js` if its shape helper needs explicit fields
- Test: manual browser/API verification, plus frontend build

**Interfaces:**
- Consumes: `GET /api/mails/<message_id>` fields `media` and `multimodal`
- Consumes: `GET /api/mails/<message_id>/media/<media_id>` preview URL

- [ ] **Step 1: Inspect current `MailDetail.vue` data flow**

Read how the page fetches mail details, displays retrieval evidence, and lays out original body plus draft.

- [ ] **Step 2: Add multimodal panel**

Add an Element Plus section showing summary, confidence, visible text, product identifiers, fault signals, risk signals, needed information, and errors. Show the panel only when media or multimodal data exists.

- [ ] **Step 3: Add media previews**

For image/frame media, render image previews using authenticated API URLs. For original videos, show filename, source, type, and size; add playback only if the existing auth/token handling makes it reliable.

- [ ] **Step 4: Run frontend build**

Run: `npm run build` from `frontend`.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/views/MailDetail.vue frontend/src/api/mail.js src/email_agent/web/dist
git commit -m "feat: show multimodal evidence in review UI"
```

---

### Task 7: Full Regression, Documentation, and Final Commit

**Files:**
- Modify: `README.md`
- Modify: `QUICKSTART.md` if startup/config instructions need updating
- Modify: `requirements.txt` only if an optional dependency note becomes necessary

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: user-facing documentation for multimodal setup and limitations.

- [ ] **Step 1: Document multimodal setup**

Update README with:

- `multimodal.enabled`
- DeepSeek vision model name
- `MULTIMODAL_API_KEY` fallback behavior
- Supported image/video sources
- Video key-frame limitation
- No public remote image downloads in v1
- Safety escalation behavior

- [ ] **Step 2: Run backend tests**

Run: `python -m unittest discover -s tests -v`

- [ ] **Step 3: Run syntax check**

Run: `python -m compileall -q src main.py web_app.py tests`

- [ ] **Step 4: Run frontend build**

Run: `npm run build` from `frontend`.

- [ ] **Step 5: Inspect git status**

Run: `git status --short`

- [ ] **Step 6: Commit documentation or final fixes**

```powershell
git add README.md QUICKSTART.md requirements.txt
git commit -m "docs: document multimodal email analysis"
```

