# History

## 1.4 - 2026-03-16

### Added

- Added lens visual index grids for all three lens categories (Z-mount 41, F-mount 46, Classic 72), using eBay listing thumbnails sorted by focal length.
- Added Nikkor lens lineup hero banner image that swaps in when any lens category tab is active; camera collection hero and manual hotspots are hidden during lens view.

### Changed

- Film camera visual index boards are now wrapped in a single foldable `<details>` element (default collapsed).
- Film camera history board images are capped at native 550px width and centered, preventing blurry upscaling.
- Recalibrated image 2 hotspot row boundaries via pixel analysis (24 → 25 rows), fixing 83–122 px downward drift that misaligned hotspots from row 14 onward (35Ti, F90X, FM3A, F6 etc.).

## 1.3 - 2026-03-16

### Added

- Expanded the F-mount lens catalog with broader AF/AF-S/AF-P coverage, including wide zoom, standard zoom, tele zoom, macro, DX, and PC-E lines.
- Added Z-mount DX lenses:
  - `NIKKOR Z DX 12-28mm f/3.5-5.6 PZ VR`
  - `NIKKOR Z DX 16-50mm f/3.5-6.3 VR`
  - `NIKKOR Z DX 18-140mm f/3.5-6.3 VR`
  - `NIKKOR Z DX 24mm f/1.7`
  - `NIKKOR Z DX 50-250mm f/4.5-6.3 VR`
- Added `Nikon TC-301` to classic accessories.
- Added early rangefinder bodies `Nikon I` and `Nikon M`.
- Added a film-body visual index on the home page using the history board images in `assets/Nikon-camera-history1.jpg` and `assets/Nikon-camera-history2.jpg`.
- Added additional film-camera entries needed to support the visual index, including Nikkorex, Nikonos, Nikkormat variants, Nikon F Photomic variants, and Nikon S3M.
- Added the MIR Nikon SLR archive link to the resources page:
  - `https://www.mir.com.my/rb/photography/companies/nikon/htmls/models/htmls/slrmain8090.htm`

### Changed

- Reworked the F-mount lens category structure to make subcategories clearer and easier to browse.
- Reclassified `Nikon S3 2000 Limited` so it is no longer shown as a rare-listing watch item.
- Improved rangefinder search coverage by using the more accurate eBay Browse API search category for Nikon rangefinder bodies.
- Regenerated catalog data, per-product histories, sitemap, and static product pages to reflect the expanded catalog.

### Fixed

- Fixed the GitHub Actions publish step so generated `resources.html` is staged and pushed along with other root site files.
- Fixed a small-set Gemini filtering edge case in `scripts/fetch_prices.py` where all listings could be dropped even when the log said the original set was being accepted.
- Corrected missing or undercounted results for rangefinder models such as `Nikon M` that were being filtered out by the old search category.
