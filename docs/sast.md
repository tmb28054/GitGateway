# SAST Pipeline

## Overview

GitGateway includes a built-in Static Application Security Testing (SAST) pipeline that runs automatically on pull request creation and updates. It uses seven tools to catch security vulnerabilities, dependency issues, and configuration problems.

## Tools

| Tool | Purpose | Findings |
|------|---------|----------|
| **Semgrep** | Code pattern matching for security anti-patterns | SQL injection, XSS, hardcoded secrets |
| **Bandit** | Python-specific security analysis | Insecure functions, shell injection, crypto issues |
| **pip-audit** | Python dependency vulnerability scanning | Known CVEs in installed packages |
| **Trivy** | Dependency and container vulnerability scanning | CVEs across multiple ecosystems |
| **Checkov** | Infrastructure-as-code security | Misconfigured CloudFormation, Terraform, K8s |
| **yamllint** | YAML syntax and style validation | Syntax errors, duplicate keys |
| **check-jsonschema** | JSON syntax validation | Malformed JSON files |

## How It Works

1. PR is created or updated
2. GitGateway triggers a CodeBuild project with the SAST buildspec
3. CodeBuild installs all tools and runs them against the repository
4. Each tool produces JSON output
5. GitGateway parses the findings and posts them as inline PR comments at the relevant file/line
6. A summary status check is posted (pass/fail)

## Pass/Fail Logic

- **PASS**: No HIGH or CRITICAL severity findings
- **FAIL**: One or more HIGH or CRITICAL findings detected

Medium, Low, and Info findings are posted as comments but do not block merge.

## AI Triage (Optional)

When AI features are enabled, each SAST finding is sent to Gemini for triage:

- Classifies as "likely true positive" or "likely false positive"
- Assigns a confidence score (0-100%)
- Provides reasoning

This reduces alert fatigue by helping developers focus on real issues.

## Configuration

SAST is enabled by default on new repositories. To disable:

- Web UI: Repository Settings → uncheck "Enable SAST scanning"
- API: `PATCH /api/<group>/<repo>` with `{"sast_enabled": false}`

## Tool Versions

Tool versions are pinned in the generated buildspec for reproducibility. Update by redeploying the stack or overriding the buildspec.
