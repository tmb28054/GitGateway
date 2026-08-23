# FAQ

## General

### What is GitGateway?
A serverless Git hosting platform that replaces self-hosted GitLab for personal and small-team use. It runs entirely on AWS serverless services (Lambda, S3, DynamoDB, API Gateway, CloudFront) with minimal operational cost.

### How much does it cost to run?
For a personal setup with low traffic: less than $3/month. Most AWS services stay within free tier limits for small-team usage.

### Can I use standard git commands?
Yes. `git clone`, `git push`, `git pull`, `git fetch` all work unchanged over HTTPS. No special client needed for repositories under ~20MB packed size.

### What about large repositories?
For repos exceeding Lambda's streaming limit (~20MB), install the `gitgateway` Python package which includes a transparent remote helper. It handles large transfers via S3 automatically.

## Authentication

### How do I log in?
Via OAuth through your configured identity provider. For git operations, use `__token__` as username and your JWT as the password.

### Can I use SSH instead of HTTPS?
Not currently. GitGateway only supports the Git Smart HTTP protocol. SSH would require a persistent server, which contradicts the serverless design.

### Can I use a different identity provider?
Yes. Any OAuth provider that issues RS256-signed JWTs with the required claims (`sub`, `gitgateway-access`, `gitgateway-admin`) will work. Configure via the `idp_url` setting.

## Repositories

### Is there a size limit?
Soft limit of ~20MB packed for seamless vanilla git operations. With the remote helper installed, there's no practical limit (S3 handles the storage).

### Can I migrate from GitHub/GitLab?
Yes. Push your existing repo to GitGateway:
```bash
git remote add gitgateway https://git.example.com/myteam/myrepo.git
git push gitgateway --all
git push gitgateway --tags
```

### Does it support Git submodules?
Yes. Submodules pointing to GitGateway repos work normally. Submodules pointing to external repos work as long as the client can reach them.

## Pull Requests

### Can I require approvals before merge?
Yes. Configure branch protection with `required_approvals` > 0.

### Can CI systems post status checks?
Yes. Any system can POST to the status checks API endpoint. The built-in SAST pipeline also posts status checks automatically.

## Troubleshooting

### "Repository not found" on clone
- Verify the repo exists: `gitgateway repo list --group <group>`
- Check your access level: `gitgateway access list <username>`
- Ensure your token hasn't expired

### Push rejected
- Check branch protection rules (may require PR)
- Verify you have `contributor` or `admin` access
- Check if the branch is protected against force-push

### SAST pipeline not running
- Verify SAST is enabled on the repo (check settings)
- Check CodeBuild project exists (deployed with `EnableCodeBuild: true`)
- Look at webhook delivery logs for errors

### AI features not working
- Verify AI is enabled on the repo
- Check that `EnableAI` was set to `true` during deployment
- Verify the Gemini API key in Secrets Manager is valid
- Check rate limits haven't been exceeded (30 calls/hour default)
