# FetchCore launch imagery — approved concept renders

This directory holds the **asset manifest** and expected paths for seven PNG images from the approved FetchCore launch visuals. **All seven original PNG files are committed alongside this manifest.** They were imported without resampling or recompression and checked against their original Git blob IDs.

| Target path | Purpose |
| --- | --- |
| `banner/hero-banner.png` | Website hero / promotional wide banner (3:1) |
| `icon/github-avatar-concept.png` | Richly rendered icon concept (use canonical vector mark at small sizes) |
| `github/social-preview.png` | Repository social preview source image (2:1) |
| `visual/repository-to-evidence.png` | Text-free illustration |
| `social/launch-post-square.png` | Square launch post |
| `explain/how-fetchcore-works.png` | Product explanation image |
| `presentation/cover-slide.png` | Presentation cover |

The original source files were imported from the handoff package and renamed to these predictable paths. Run `python scripts/brand/verify_launch_assets.py` to validate exact SHA-256 checksums and image dimensions. Images are generated *marketing concepts*, **not actual FetchCore application screenshots**. This guide does not claim benchmark results, endorsement by pictured vendors, or graph-backed/federated retrieval.

**Canonical branding:** `assets/brand/` SVGs remain the source of truth for marks. The artwork uses a supporting 3D visual language and does not change product scope.

**Before launch:** verify the imagery in light/dark presentation surfaces and manually configure the GitHub social preview from `github/social-preview.png`, perform crop/legibility checks, and ensure all appearance-related metadata is consistent. Asset rights/public release decision remains bounded by the project's license issue #110.

Related: #106 (identity), #107 (social preview), #96 (launch).
