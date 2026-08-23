# Deployment Guide

## Prerequisites

- AWS account with permissions to create CloudFormation stacks, S3 buckets, DynamoDB tables, Lambda functions, API Gateway, and CloudFront distributions.
- A registered domain name with DNS managed in Route 53 (or ability to create CNAME records).
- An ACM certificate for your domain in the `us-east-1` region (required for CloudFront).
- Python 3.10+ with `gitgateway` installed.

## Step 1: Install GitGateway

```bash
pip install gitgateway
```

## Step 2: Request ACM Certificate

If you don't have one already:

```bash
aws acm request-certificate \
    --domain-name git.example.com \
    --validation-method DNS \
    --region us-east-1
```

Complete the DNS validation, then note the certificate ARN.

## Step 3: Deploy Infrastructure

```bash
gitgateway deploy \
    --domain-name git.example.com \
    --acm-certificate-arn arn:aws:acm:us-east-1:123456789012:certificate/abc-def \
    --region us-east-1
```

This creates:
- 2 S3 buckets (repos + LFS, KMS encrypted)
- 9 DynamoDB tables (pay-per-request billing)
- API Gateway HTTP API
- CloudFront distribution with your custom domain

Deployment takes approximately 5-10 minutes.

## Step 4: Configure DNS

Create a CNAME record pointing your domain to the CloudFront distribution:

```
git.example.com → d1234abcdef.cloudfront.net
```

The CloudFront domain is shown in the deployment outputs.

## Step 5: Configure the CLI

```bash
gitgateway configure --from-stack gitgateway --region us-east-1
```

## Step 6: Configure Identity Provider

Add GitGateway as an OAuth service in your IdP's `services.yaml`:

```yaml
oauth:
  gitgateway:
    url: https://git.example.com/auth/callback
    client_id: gitgateway
    scopes: ["openid", "profile"]
    token_expiry_minutes: 60
```

Ensure the IdP issues JWTs with `gitgateway-access` and `gitgateway-admin` claims.

## Step 7: Create Your First Repository

```bash
gitgateway group create myteam
gitgateway repo create myteam/my-first-repo
git clone https://git.example.com/myteam/my-first-repo.git
```

## Updating

To update an existing deployment:

```bash
gitgateway deploy \
    --domain-name git.example.com \
    --acm-certificate-arn arn:aws:acm:... \
    --region us-east-1
```

CloudFormation will detect changes and update only what's needed.

## Cost Estimate

For a personal/small-team setup with low traffic:

| Service | Estimated Monthly Cost |
|---------|----------------------|
| DynamoDB | $0 (free tier covers ~25 RCU/WCU) |
| S3 | $0.01-0.10 (depending on repo sizes) |
| Lambda | $0 (free tier covers ~1M requests) |
| API Gateway | $0-1.00 |
| CloudFront | $0-1.00 |
| **Total** | **< $3/month** |
