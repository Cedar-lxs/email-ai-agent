# After-Sales Priority Hardening Design

## Goal

Improve the after-sales email agent's production readiness in four high-priority areas:

1. Decision audit for auto-send, draft, and human escalation.
2. Multimodal diagnostic tracing for DeepSeek image analysis.
3. Text extraction and review display for ordinary customer attachments.
4. Structured product indexing for model-first knowledge matching.

The work should preserve the current email processing flow and keep existing safety rules intact. Low-risk technical emails may auto-send in full-auto mode even when they include attachments, but media or attachment processing errors, visual risk signals, business intent, high urgency, angry sentiment, and high-risk operations still block auto-send.

## Current State

The system already supports:

- IMAP email ingestion and SMTP replies.
- Intent classification and low-risk auto-send gates.
- Hybrid knowledge retrieval with lexical, identifier, and vector signals.
- Bocha web-search fallback for low-risk technical questions.
- DeepSeek multimodal analysis for email images and video key frames.
- Web review pages showing original email, draft, evidence, media previews, and multimodal observations.

Recent debugging showed that DeepSeek vision may return useful image-reading details in `reasoning_content` while `content` is empty or non-JSON. The analyzer now falls back to parsing `reasoning_content`, but the system still needs better observability so similar problems are visible without manual database inspection.

## Non-Goals

- Do not build a full ticketing system.
- Do not auto-send business, refund, warranty, order, legal, safety, or angry/high-urgency replies.
- Do not automatically extract or execute archive files in the first phase.
- Do not store API keys, customer secrets, or full model reasoning traces.
- Do not replace the existing knowledge retriever; product indexing should complement it.

## Design

### 1. Decision Audit

Add a structured decision audit object for every processed email.

The audit should record:

- Final action: `auto_sent`, `draft_ready`, `escalated`, `failed`, or `skipped`.
- Whether workflow mode allowed auto-send.
- Intent, sentiment, urgency, and whether the intent is in the auto-reply allowlist.
- Whether local knowledge met the confidence threshold.
- Whether Bocha fallback was used.
- Whether media or attachment processing succeeded.
- Whether multimodal risk signals were found.
- Human-readable blocking reasons.

Store this object in a new database column, `decision_trace TEXT DEFAULT ''`, and expose it through `GET /api/mails/<message_id>`.

The Web mail detail page should display the audit as a compact "处理决策" section with status tags and blocking reasons. This should be an operational view, not a marketing explanation.

### 2. Multimodal Diagnostics

Extend the existing multimodal trace with a `diagnostics` object.

The diagnostics should include:

- Provider and model name.
- Number of images sent.
- Which response field was parsed: `content`, `reasoning_content`, or fallback extraction.
- Whether JSON parsing succeeded.
- Which structured fields were populated.
- Redacted raw response preview limited to a small character count.
- Error messages, if any.

This diagnostic data is for debugging only. It must not include API keys or full model reasoning. It can be stored alongside `multimodal_trace` rather than in a new table.

### 3. Ordinary Attachment Text Extraction

Add an attachment extraction path for non-media customer files.

Supported in the first phase:

- PDF: extract readable text from pages.
- DOCX: extract paragraph and table text.
- XLSX/CSV: extract sheet names, headers, and a limited number of rows.
- Other files and archives: store metadata only and show them in the Web UI, without automatic extraction.

New domain model:

- `EmailAttachment`: attachment ID, filename, content type, size, source, data, metadata.
- `StoredAttachment`: saved path plus extracted text and extraction status.

The mail fetcher should separate media attachments from ordinary attachments. The storage layer should save ordinary attachments under `data/attachments/<message-hash>/`.

Extracted text should be:

- Stored in an attachment manifest column, `attachment_manifest TEXT DEFAULT ''`.
- Included in retrieval text and reply-generation context.
- Truncated by configuration to avoid huge prompts.

Extraction errors should not crash processing. In full-auto mode, attachment extraction errors block auto-send, because the attachment may contain important customer context.

The Web mail detail page should show ordinary attachments with filename, size, type, extraction status, and a short text preview. Download or preview routes must serve only manifest-listed local files.

### 4. Structured Product Index

Add a product index that is built from the existing knowledge base.

Initial source:

- Structured JSON files under `knowledge/结构化数据`.
- Markdown headings and metadata from product knowledge files.
- Existing identifiers discovered by the retriever.

Product records should include:

- Model number.
- Product family or category.
- Managed/unmanaged type when known.
- Port composition.
- PoE capability or power information when known.
- Version strings.
- Source file and section.

Retrieval flow:

1. Build the normal `RetrievalQuery`.
2. If the email body, multimodal labels, or attachment text contain model-like identifiers, look up product records first.
3. Add matched product facts to retrieval keywords and reply context.
4. Continue through the existing hybrid retriever.

The product index should not invent facts from web search or model output. It should only represent local knowledge. If DeepSeek label data conflicts with the product index, record the conflict in the decision audit and avoid auto-send.

## Data Flow

1. Mail fetcher parses text, media, and ordinary attachments.
2. Media is saved and sent to multimodal analysis.
3. Ordinary attachments are saved and text-extracted when supported.
4. Intent classification runs on customer email text.
5. Retrieval query combines translated email text, multimodal label fields, attachment text, and product-index facts.
6. Local knowledge retrieval runs first.
7. Bocha fallback runs only when local knowledge is insufficient and the question is low-risk.
8. Reply generation receives knowledge, multimodal context, attachment context, product facts, and conversation history.
9. Decision audit records why the final action was chosen.
10. Web API and frontend expose media, attachments, multimodal trace, retrieval trace, and decision trace.

## Safety Rules

Auto-send is allowed only when all are true:

- Workflow mode is `full_auto`.
- Intent is in `workflow.auto_reply_types`.
- Intent is not business, angry, or high urgency.
- Customer text does not request high-risk operations such as firmware upgrade, factory reset, data deletion, unsafe power changes, disassembly, or electrical safety handling.
- Media analysis has no errors.
- Attachment extraction has no errors for supported attachment types.
- Multimodal risk signals are empty.
- Product index facts do not conflict with extracted label facts.
- If Bocha fallback was used, `web_search.auto_send_low_risk` is true.

Attachment presence alone must not block auto-send for low-risk technical issues.

## Configuration

Add or extend configuration:

```yaml
attachments:
  enabled: true
  max_attachments_per_email: 8
  max_attachment_bytes: 10485760
  max_extracted_chars: 6000
  extract_pdf: true
  extract_docx: true
  extract_xlsx: true

product_index:
  enabled: true
  path: "./data/product_index.json"
```

## Testing

Add focused tests before production code:

- Database migration and round-trip for `decision_trace` and `attachment_manifest`.
- Mail fetcher separates media from ordinary attachments.
- PDF/DOCX/XLSX extraction returns bounded text and records errors safely.
- Decision audit records auto-send, draft, and escalation reasons.
- Full-auto low-risk attachment email can auto-send.
- Attachment extraction error blocks auto-send.
- Multimodal diagnostics records `reasoning_content` fallback.
- Product index matches model identifiers and contributes facts to retrieval.
- Product conflict blocks auto-send.
- API mail detail includes decision trace and attachment manifest.

Run full backend tests and frontend production build before completion.

## Rollout

Implement in this order:

1. Decision audit and multimodal diagnostics.
2. Ordinary attachment parsing, storage, extraction, API, and frontend display.
3. Product index generation and retrieval integration.
4. Documentation and verification.

Each stage should keep the current system runnable and should not require a database reset.
