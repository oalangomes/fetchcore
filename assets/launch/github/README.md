# GitHub storefront — FetchCore (issue #107)

This is the **configuration runbook** for the approved FetchCore visuals. The image is already committed in `assets/launch/github/social-preview.png`. **Adding the PNG to Git does not automatically configure the GitHub repository's Social Preview.**

## Approved assets

- **Official source image:** [`social-preview.png`](social-preview.png) — 1774×887 (exactly 2:1). Keep this original intact; it is listed in [`assets/launch/manifest.json`](../manifest.json).
- **Canonical logo:** [`assets/brand/`](../../brand/). The enhanced 3D artwork is only supporting illustration, not a replacement for the vector mark.
- **Optional GitHub-ready export:** 1280×640 PNG, same 2:1 composition, no cropping or regeneration. Produce with:

```bash
python -m pip install Pillow
python scripts/brand/export_social_preview.py --output /tmp/fetchcore-social-preview.png
```

For the current approved source, the export was visually inspected at **1280×640** and reduced to **640×320** and **320×160**: logo and primary tagline remain visible; smaller supporting copy becomes less legible at 320px. Review on actual GitHub sharing cards. The optional export uses Pillow, and byte hashes may differ across versions; the original source hash is checked before resize.

## Repository About / Topics — administrator action

These values **cannot be applied by committing this document**. In the GitHub repository's About box, choose **Edit** and set:

- **Description:** `Backend-neutral repository knowledge and retrieval. Repositories into evidence.`
- **Website:** leave blank until an actual public landing page exists.
- **Topics:** `code-search`, `repository-knowledge`, `mcp`, `developer-tools`, `retrieval`, `python`.

An authenticated repo administrator can also apply the two metadata changes with GitHub CLI (review before running):

```bash
gh auth status
gh api --method PATCH repos/oalangomes/fetchcore \
  -f description='Backend-neutral repository knowledge and retrieval. Repositories into evidence.'
gh api --method PUT repos/oalangomes/fetchcore/topics \
  -f 'names[]=code-search' \
  -f 'names[]=repository-knowledge' \
  -f 'names[]=mcp' \
  -f 'names[]=developer-tools' \
  -f 'names[]=retrieval' \
  -f 'names[]=python'

gh api repos/oalangomes/fetchcore --jq '{description, topics, private}'
```

The topic operation sets the entire collection, so add any existing topics to the command before running. **Never switch repository visibility in this task.**

## Social Preview — administrator action

1. Open the repository **Settings → General → Social preview** (GitHub UI; menu labels may vary).
2. Upload the **approved** `social-preview.png` or the optional 1280×640 export. Both have the same 2:1 aspect ratio.
3. Inspect the thumbnail **inside GitHub settings**. Confirm logo, tagline, and the visual representation of repository input → evidence.
4. Recheck the shared-card preview in at least two sizes (desktop and compact/mobile). The repository remains private; anonymous public validation will happen only after a separate launch approval.
5. Record a short completion comment in [issue #107](https://github.com/oalangomes/fetchcore/issues/107) with a screenshot of the configured preview **if permissible**, the About text, topics and date.

## Product boundaries

The supporting images are **concept art**, not product screenshots. They do not assert agent orchestration, graph-backed indexing, satisfaction or execution authority. FetchCore retrieves typed candidates/evidence with provenance; **retrieval ≠ satisfaction ≠ authority**.

This task does not alter CI, backend providers, retrieval performance, or the public visibility of FetchCore.

## Verification checklist

- [ ] About description matches above
- [ ] The six topics appear on repository overview
- [ ] Social Preview is configured **in GitHub Settings**, not just committed to Git
- [ ] 2:1 preview has acceptable legibility and safe cropping
- [ ] No misleading capability claims or unapproved third-party imagery
- [ ] Visibility remains private until public-readiness checklist #110/#111

Handoff: [#108](https://github.com/oalangomes/fetchcore/issues/108) owns bilingual README and first-run onboarding.
