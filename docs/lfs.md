# Git LFS

## Overview

GitGateway supports Git Large File Storage (LFS) for managing binary files. LFS objects are stored directly in S3 via presigned URLs — Lambda never handles the large binary data, keeping operations fast and within Lambda's payload limits.

## How It Works

1. Git LFS client sends a Batch API request listing objects to upload/download
2. GitGateway generates presigned S3 URLs for each object
3. LFS client uploads/downloads directly to/from S3
4. Objects are stored with content-addressable paths: `lfs/<group>/<repo>/<oid[0:2]>/<oid[2:4]>/<oid>`

## Setup

Git LFS works automatically with GitGateway. Configure your repo:

```bash
git lfs install
git lfs track "*.psd"
git lfs track "*.zip"
git add .gitattributes
git commit -m "Track large files with LFS"
```

## File Locking

GitGateway supports the LFS lock API for coordinating binary file edits:

```bash
# Lock a file
git lfs lock assets/design.psd

# List locks
git lfs locks

# Unlock
git lfs unlock assets/design.psd
```

Force-unlock (admin):
```bash
git lfs unlock assets/design.psd --force
```

## Storage

LFS objects are stored in a dedicated S3 bucket (`gitgateway-lfs`) with KMS encryption. The bucket is separate from the git repo storage to allow independent lifecycle policies.

## Presigned URL Expiry

Upload and download URLs expire after 1 hour (3600 seconds). If a transfer is interrupted, the client will request new URLs on retry.
