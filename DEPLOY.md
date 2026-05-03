# Deploy

## GitHub Pages

1. Create or use public repo `BobsPorkAndBeans/sol007-dashboard`.
2. Push this directory to the repo default branch (`main`).
3. In GitHub: **Settings → Pages → Build and deployment**.
4. Source: **Deploy from a branch**.
5. Branch: `main`, folder `/ (root)`, then **Save**.
6. Public URL will be `https://bobsporkandbeans.github.io/sol007-dashboard/` after Pages finishes.

CLI equivalent when authenticated:

```bash
gh repo create BobsPorkAndBeans/sol007-dashboard --public --source ~/sol007-dashboard --remote origin --push
gh api -X POST repos/BobsPorkAndBeans/sol007-dashboard/pages \
  -f source.branch=main -f source.path=/
```

If Pages already exists, update it with:

```bash
gh api -X PUT repos/BobsPorkAndBeans/sol007-dashboard/pages \
  -f source.branch=main -f source.path=/
```

## Optional custom domain: sol007.naber-research.com

1. In the repo Pages settings, set custom domain to `sol007.naber-research.com` and enable **Enforce HTTPS** after DNS verifies.
2. Add this DNS record at the domain provider:

```text
CNAME sol007 BobsPorkAndBeans.github.io
```

3. Add a `CNAME` file containing `sol007.naber-research.com` to the repo root only when ready to cut over.

Do not publish private key locations, Telegram channel IDs, OpenClaw session IDs, or local machine paths.
