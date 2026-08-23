# AI Features

## Overview

GitGateway integrates with Google Gemini to provide AI-powered code intelligence. All AI features are opt-in per repository and clearly marked as AI-generated.

## Features

### PR Auto-Summarization

When a PR is created, Gemini generates a 2-4 sentence summary describing what changed and why, based on the diff and commit messages. Posted as the first comment by the `gitgateway-ai` bot user.

### Advisory Code Review

Gemini reviews the diff for potential bugs, security issues, and style problems. Findings are posted as inline comments with severity levels (info/warning/error). These are advisory only and never block merge.

### SAST Triage

After SAST findings are generated, Gemini evaluates each finding in context to classify it as likely true or false positive, with a confidence score and reasoning.

### Semantic Code Search

Code is chunked and embedded using Gemini's `text-embedding-004` model. Users can search with natural language queries (e.g., "where do we handle authentication errors?") and get relevant code chunks ranked by similarity.

## Configuration

### Enable AI for a Repository

- Web UI: Repository Settings → check "Enable AI features"
- API: `PATCH /api/<group>/<repo>` with `{"ai_enabled": true}`

### Gemini API Key

Store your Gemini API key in AWS Secrets Manager. The ARN is provided as the `GeminiApiKeyArn` CloudFormation parameter.

### Rate Limiting

AI calls are rate-limited per repository (default: 30 calls/hour) to prevent runaway costs. Configurable via the AI configuration.

## Cost Considerations

- Gemini API calls are billed per token
- PR summaries: ~500-1000 tokens per call
- Code review: ~1000-3000 tokens per call (depends on diff size)
- Embeddings: ~100 tokens per chunk
- Typical monthly cost for a small team: $1-5

## Privacy

- Code diffs are sent to Google's Gemini API for processing
- No data is stored by Google beyond the API call
- Consider this when working with sensitive/proprietary code
- AI features can be completely disabled per repository
