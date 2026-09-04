# After-Sales Priority Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-hardening features for decision audit, multimodal diagnostics, ordinary attachment extraction, and model-first product matching in the after-sales email agent.

**Architecture:** Extend the current layered Python app without replacing the existing processing flow. Add trace columns and small focused helpers, keep attachment extraction separate from media storage, and feed extracted facts into the existing retrieval and reply-generation path.

**Tech Stack:** Python 3.13, SQLite, Flask, Vue 3, Element Plus, python-docx, openpyxl, optional pypdf/pdfplumber when available.

**Spec:** `docs/superpowers/specs/2026-09-04-after-sales-priority-hardening-design.md`

## Global Constraints

- Low-risk technical emails may auto-send in full-auto mode even when they include attachments.
- Media or attachment processing errors, visual risk signals, business intent, high urgency, angry sentiment, and high-risk operations block auto-send.
- Do not store API keys, customer secrets, or full model reasoning traces.
- Archive files are metadata-only in this phase; do not automatically extract them.
- Every new behavior must have a failing test before production code.
- Existing databases must migrate in place; no reset is allowed.

---

## File Structure

- `src/email_agent/domain/models.py`: add ordinary attachment models and optional product/audit domain structures if needed.
- `src/email_agent/infrastructure/database.py`: add `decision_trace` and `attachment_manifest` columns plus JSON helpers.
- `src/email_agent/application/decision_audit.py`: new focused builder for decision trace objects.
- `src/email_agent/infrastructure/multimodal.py`: add safe diagnostics to analyzer output.
- `src/email_agent/infrastructure/mail_fetcher.py`: parse ordinary attachments separately from media.
- `src/email_agent/infrastructure/attachment_storage.py`: save ordinary attachments and extract bounded text.
- `src/email_agent/infrastructure/product_index.py`: build and query structured product facts from local knowledge.
- `src/email_agent/application/email_service.py`: orchestrate audit, attachments, product facts, retrieval, and auto-send gates.
- `src/email_agent/infrastructure/llm.py`: include attachment/product context in reply prompt if needed.
- `src/email_agent/web/routes/api.py`: expose decision trace, attachment manifest, and attachment download route.
- `frontend/src/views/MailDetail.vue`: display processing decision and ordinary attachments.
- `config.yaml`, `.env.example`, `README.md`, `QUICKSTART.md`: document new settings and behavior.
- Tests:
  - `tests/test_decision_audit.py`
  - `tests/test_attachment_storage.py`
  - `tests/test_attachment_mail_fetcher.py`
  - `tests/test_product_index.py`
  - Update existing multimodal, email service, API, and web-search tests.

---

### Task 1: Database Trace Columns

**Files:**
- Modify: `src/email_agent/infrastructure/database.py`
- Test: `tests/test_multimodal_api.py` or new `tests/test_decision_audit.py`

**Interfaces:**
- Produces: `EmailDB.save_decision_trace(message_id: str, trace: dict) -> None`
- Produces: `EmailDB.parse_decision_trace(row) -> dict`
- Produces: `EmailDB.save_attachment_manifest(message_id: str, manifest: list[dict]) -> None`
- Produces: `EmailDB.parse_attachment_manifest(row) -> list[dict]`

- [ ] **Step 1: Write failing migration and round-trip tests**

Add tests that create a fresh `EmailDB`, inspect `PRAGMA table_info(processed_emails)`, and assert `decision_trace` and `attachment_manifest` exist. Insert a processed email, save both JSON values, read the row, and assert helpers return the same values.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_decision_audit -v`

Expected: failure because the columns and helper methods do not exist.

- [ ] **Step 3: Implement minimal database support**

Add both columns to the create-table SQL and migration map. Implement the four JSON helper methods following the existing `save_media_manifest`, `save_multimodal_trace`, and parse helper style.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m unittest tests.test_decision_audit -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/email_agent/infrastructure/database.py tests/test_decision_audit.py
git commit -m "feat: store decision and attachment traces"
```

---

### Task 2: Decision Audit Builder

**Files:**
- Create: `src/email_agent/application/decision_audit.py`
- Test: `tests/test_decision_audit.py`
- Modify: `src/email_agent/application/email_service.py`

**Interfaces:**
- Consumes: `IntentResult`, retrieval hit counts, `used_web_search`, media count, attachment errors, multimodal observation.
- Produces: `build_decision_trace(...) -> dict`
- Produces trace keys: `action`, `mode`, `intent`, `sentiment`, `urgency`, `auto_allowed_intent`, `local_knowledge_hits`, `used_web_search`, `media_count`, `attachment_count`, `blocking_reasons`, `signals`.

- [ ] **Step 1: Write failing builder tests**

Test at least three cases:

- Auto-sent low-risk technical mail records no blocking reasons.
- Draft mail records a blocking reason when mode is `semi_auto`.
- Escalated mail records business/high-risk reason.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_decision_audit -v`

Expected: failure because `decision_audit.py` does not exist.

- [ ] **Step 3: Implement builder**

Create a small pure function that accepts explicit primitive arguments. Keep it independent from `EmailAgent` so it is easy to test.

- [ ] **Step 4: Integrate into email processing**

In `_process_email_async` and `_process_email`, save a decision trace before every final `update_status` return path:

- skipped self
- escalated by intent/risk
- escalated by visual risk
- escalated by missing knowledge
- escalated by reply generation failure
- auto-sent
- draft-ready
- failed through `_record_failure`

Use helper methods to avoid duplicating large dictionaries.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest tests.test_decision_audit tests.test_multimodal_email_service tests.test_email_service -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/email_agent/application/decision_audit.py src/email_agent/application/email_service.py tests/test_decision_audit.py tests/test_email_service.py tests/test_multimodal_email_service.py
git commit -m "feat: record email decision audit"
```

---

### Task 3: Multimodal Diagnostics

**Files:**
- Modify: `src/email_agent/infrastructure/multimodal.py`
- Test: `tests/test_multimodal_analyzer.py`

**Interfaces:**
- Produces: `MultimodalObservation.raw_items` remains unchanged.
- Produces: `MultimodalObservation` gains `diagnostics: dict[str, Any] = field(default_factory=dict)`.
- Diagnostics keys: `provider`, `model`, `image_count`, `parsed_from`, `json_parsed`, `fallback_used`, `populated_fields`, `response_preview`, `errors`.

- [ ] **Step 1: Write failing diagnostics tests**

Add tests for:

- Normal JSON in `content` records `parsed_from == "content"` and populated fields.
- Empty `content` with useful `reasoning_content` records `parsed_from == "reasoning_content"` and `fallback_used == True`.
- API failure records diagnostics errors without API key.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_multimodal_analyzer -v`

Expected: failure because `diagnostics` is missing.

- [ ] **Step 3: Implement diagnostics**

Add diagnostics to `MultimodalObservation` and populate it in `MultimodalAnalyzer`. Limit `response_preview` to 500 characters after removing obvious secrets and line noise. Do not store full `reasoning_content`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_multimodal_analyzer tests.test_multimodal_email_service -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/email_agent/domain/models.py src/email_agent/infrastructure/multimodal.py tests/test_multimodal_analyzer.py tests/test_multimodal_email_service.py
git commit -m "feat: add multimodal diagnostics"
```

---

### Task 4: Ordinary Attachment Parsing

**Files:**
- Modify: `src/email_agent/domain/models.py`
- Modify: `src/email_agent/infrastructure/mail_fetcher.py`
- Test: `tests/test_attachment_mail_fetcher.py`

**Interfaces:**
- Produces: `EmailAttachment(attachment_id, filename, content_type, source, size_bytes=0, data=b"", metadata={})`
- Produces: `ParsedEmail.attachments: list[EmailAttachment]`

- [ ] **Step 1: Write failing MIME parsing tests**

Create raw MIME emails for:

- One PDF attachment.
- One DOCX attachment.
- One image attachment plus one PDF attachment.

Assert image goes to `ParsedEmail.media` and PDF/DOCX go to `ParsedEmail.attachments`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_attachment_mail_fetcher -v`

Expected: failure because ordinary attachment model/parsing does not exist.

- [ ] **Step 3: Implement parsing**

Add attachment model, add `attachments` field to `ParsedEmail`, and update `MailFetcher._parse_raw_email` to collect non-media attachment parts with decoded payloads. Keep inline text parts unchanged.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_attachment_mail_fetcher tests.test_multimodal_mail_fetcher -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/email_agent/domain/models.py src/email_agent/infrastructure/mail_fetcher.py tests/test_attachment_mail_fetcher.py
git commit -m "feat: parse ordinary email attachments"
```

---

### Task 5: Attachment Storage And Extraction

**Files:**
- Create: `src/email_agent/infrastructure/attachment_storage.py`
- Test: `tests/test_attachment_storage.py`
- Modify: `config.yaml`
- Modify: `.env.example` only if a new optional parser dependency needs an env toggle.

**Interfaces:**
- Produces: `StoredAttachment(attachment_id, filename, content_type, source, path, size_bytes, extracted_text="", extraction_status="not_extracted", extraction_error="", metadata={})`
- Produces: `AttachmentStorage(root: Path, config: dict)`
- Produces: `AttachmentStorage.save(message_id: str, attachments: list[EmailAttachment]) -> tuple[list[StoredAttachment], list[str]]`
- Produces: `AttachmentStorage.manifest(stored: list[StoredAttachment]) -> list[dict]`
- Produces: `format_attachment_context(stored: list[StoredAttachment], max_chars: int) -> str`

- [ ] **Step 1: Write failing storage tests**

Test:

- PDF/DOCX/XLSX/CSV files are saved under `data/attachments/<hash>/`.
- Unsupported archive files are saved as metadata with `extraction_status == "metadata_only"`.
- Size/count limits produce errors.
- Extracted text is truncated to configured maximum.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_attachment_storage -v`

Expected: failure because storage does not exist.

- [ ] **Step 3: Implement storage and extractors**

Use safe filename logic similar to media storage. For extraction:

- PDF: use `pypdf` first; if unavailable or fails, record extraction error.
- DOCX: use `python-docx`.
- XLSX: use `openpyxl` read-only mode.
- CSV: decode as UTF-8 with replacement and read bounded lines.

Do not add heavyweight external downloads during implementation.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_attachment_storage -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/email_agent/infrastructure/attachment_storage.py tests/test_attachment_storage.py config.yaml .env.example
git commit -m "feat: store and extract email attachments"
```

---

### Task 6: Attachment Integration In Email Flow

**Files:**
- Modify: `src/email_agent/application/email_service.py`
- Modify: `src/email_agent/infrastructure/llm.py`
- Test: `tests/test_multimodal_email_service.py`
- Test: `tests/test_email_service.py`

**Interfaces:**
- Consumes: `AttachmentStorage.save`, `format_attachment_context`, database attachment helpers.
- Produces: attachment context included in retrieval keywords/text and reply prompt.
- Produces: extraction errors block auto-send but still allow semi-auto draft.

- [ ] **Step 1: Write failing flow tests**

Add tests:

- Low-risk full-auto email with a readable attachment can auto-send.
- Attachment extraction error results in draft-ready instead of auto-sent.
- Retrieval query includes extracted attachment text.
- Reply generation receives attachment context.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m unittest tests.test_multimodal_email_service tests.test_email_service -v
```

Expected: failure because email flow ignores ordinary attachments.

- [ ] **Step 3: Implement flow integration**

Initialize `AttachmentStorage` in `EmailAgent.__init__`. Add `_prepare_attachments_async` and `_prepare_attachments` mirroring `_prepare_multimodal`. Save manifest, return stored attachments, context, and errors.

Update retrieval query construction:

- Append extracted attachment text to retrieval body or summary.
- Add short attachment-derived keywords from filenames and extracted text.

Update `AIProcessor.generate_reply` and `generate_reply_async` to accept `attachment_context: str = ""`.

- [ ] **Step 4: Update auto-send gate**

Update `_can_auto_send` signature to include `attachment_errors: list[str] = None`. Return false when errors exist. Do not block solely because attachments exist.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest tests.test_multimodal_email_service tests.test_email_service tests.test_attachment_storage -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/email_agent/application/email_service.py src/email_agent/infrastructure/llm.py tests/test_multimodal_email_service.py tests/test_email_service.py
git commit -m "feat: use attachment text in email diagnosis"
```

---

### Task 7: Product Index

**Files:**
- Create: `src/email_agent/infrastructure/product_index.py`
- Test: `tests/test_product_index.py`
- Modify: `src/email_agent/application/email_service.py`
- Modify: `config.yaml`

**Interfaces:**
- Produces: `ProductRecord(model, family="", management_type="unknown", ports=[], poe="", versions=[], source="", section="")`
- Produces: `ProductIndex.from_knowledge(knowledge_dir: Path) -> ProductIndex`
- Produces: `ProductIndex.find(identifiers: list[str]) -> list[ProductRecord]`
- Produces: `format_product_context(records: list[ProductRecord]) -> str`
- Produces: `detect_product_conflicts(records: list[ProductRecord], observation: MultimodalObservation | None) -> list[str]`

- [ ] **Step 1: Write failing product index tests**

Use a temporary knowledge directory with:

- `结构化数据/products.vector.json` containing a GPS208-like product record.
- A markdown file under `03-云网管交换机/GPS208.md` with title and port information.

Assert lookup by `GPS208` returns model, management type, source, and port facts.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_product_index -v`

Expected: failure because product index does not exist.

- [ ] **Step 3: Implement product index**

Parse structured JSON defensively by accepting common field names such as `model`, `型号`, `product_model`, `category`, `management_type`, `ports`, `port_composition`, `poe`, `versions`. Parse markdown from path/category and model-like identifiers in headings/content.

- [ ] **Step 4: Integrate retrieval enrichment**

In `EmailAgent`, build or load the product index when enabled. Before retrieval, find products from text identifiers plus multimodal identifiers. Add product facts to retrieval keywords and reply context.

- [ ] **Step 5: Add conflict safety**

If multimodal says managed and product index says unmanaged, or vice versa, add a decision audit blocking reason and prevent auto-send.

- [ ] **Step 6: Run tests**

Run:

```bash
python -m unittest tests.test_product_index tests.test_multimodal_email_service tests.test_web_search -v
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/email_agent/infrastructure/product_index.py src/email_agent/application/email_service.py tests/test_product_index.py config.yaml
git commit -m "feat: add structured product index"
```

---

### Task 8: API And Frontend Display

**Files:**
- Modify: `src/email_agent/web/routes/api.py`
- Modify: `frontend/src/api/mail.js`
- Modify: `frontend/src/views/MailDetail.vue`
- Test: `tests/test_multimodal_api.py`

**Interfaces:**
- Consumes: `EmailDB.parse_decision_trace(row)`, `EmailDB.parse_attachment_manifest(row)`
- Produces: `GET /api/mails/<message_id>` includes `decision` and `attachments`.
- Produces: `GET /api/mails/<message_id>/attachments/<attachment_id>` safe download route.

- [ ] **Step 1: Write failing API tests**

Insert a processed email with decision trace and attachment manifest. Assert detail response includes both. Create a local attachment file and assert the download route serves only manifest-listed files under the attachment root.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_multimodal_api -v`

Expected: failure because the API does not expose decision/attachments.

- [ ] **Step 3: Implement API**

Add response fields and a safe attachment route modeled after the media route. Token handling should match media route.

- [ ] **Step 4: Update frontend**

In mail detail:

- Add "处理决策" section showing final action, blocking reasons, knowledge source, Bocha usage, media status, and attachment status.
- Add ordinary attachment list with file type, size, extraction status, text preview, and download link.
- Keep the existing image/video section.

- [ ] **Step 5: Build frontend**

Run:

```bash
C:\Users\Cedar\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe node_modules\vite\bin\vite.js build
```

Expected: production build succeeds and updates `src/email_agent/web/dist`.

- [ ] **Step 6: Commit**

```bash
git add src/email_agent/web/routes/api.py frontend/src/api/mail.js frontend/src/views/MailDetail.vue src/email_agent/web/dist tests/test_multimodal_api.py
git commit -m "feat: show decision and attachments in review UI"
```

---

### Task 9: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `QUICKSTART.md`
- Modify: `.env.example`
- Modify: `config.yaml`

**Interfaces:**
- Documents: attachment config, product index config, decision audit display, and auto-send safety behavior.

- [ ] **Step 1: Update docs**

Add concise sections explaining:

- What the decision audit shows.
- Which attachment formats are extracted.
- Why archives are metadata-only.
- How product index improves model matching.
- Full-auto safety gates.

- [ ] **Step 2: Run complete backend verification**

Run:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src main.py web_app.py tests
git diff --check
```

Expected: all tests pass, compileall exits 0, diff check exits 0.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend
C:\Users\Cedar\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe node_modules\vite\bin\vite.js build
```

Expected: build exits 0. Existing Vite deprecation/chunk-size warnings are acceptable if unchanged.

- [ ] **Step 4: Final code review**

Use `superpowers:requesting-code-review`. Reviewer should focus on:

- Auto-send safety.
- Attachment download path safety.
- No API key or full reasoning leakage.
- Product-index conflict behavior.
- Database migration compatibility.

- [ ] **Step 5: Fix review findings**

Fix Critical and Important findings, with tests first when behavior changes.

- [ ] **Step 6: Final commit**

```bash
git add README.md QUICKSTART.md .env.example config.yaml tests src frontend/src src/email_agent/web/dist
git commit -m "docs: document after-sales hardening"
```

---

## Self-Review

- Spec coverage: decision audit, multimodal diagnostics, ordinary attachments, product index, API/frontend exposure, docs, and verification are covered.
- Placeholder scan: no unfinished placeholder markers are intentionally left.
- Type consistency: database helper names and trace field names are defined before later tasks consume them.
- Safety: full-auto remains gated by mode, intent allowlist, sentiment/urgency, high-risk operations, media errors, attachment errors, visual risk signals, product conflicts, and Bocha auto-send setting.
