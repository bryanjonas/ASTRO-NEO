#!/usr/bin/env bash
set -e

BASE_URL="http://data.astrometry.net/4200"
OUTDIR="./data/indexes"

mkdir -p "$OUTDIR"

# Index levels needed for BOTH rigs:
#  - C8 + reducer requires: 4204, 4205, 4206, 4207, 4208, 4209
#  - RedCat 51 also uses:   4206, 4207, 4208, 4209, 4210, 4211, 4212
#  - Optional insurance:    4213

FILES=(
  # 4204 (48 tiles)
  index-4204-00.fits index-4204-01.fits index-4204-02.fits index-4204-03.fits
  index-4204-04.fits index-4204-05.fits index-4204-06.fits index-4204-07.fits
  index-4204-08.fits index-4204-09.fits index-4204-10.fits index-4204-11.fits
  index-4204-12.fits index-4204-13.fits index-4204-14.fits index-4204-15.fits
  index-4204-16.fits index-4204-17.fits index-4204-18.fits index-4204-19.fits
  index-4204-20.fits index-4204-21.fits index-4204-22.fits index-4204-23.fits
  index-4204-24.fits index-4204-25.fits index-4204-26.fits index-4204-27.fits
  index-4204-28.fits index-4204-29.fits index-4204-30.fits index-4204-31.fits
  index-4204-32.fits index-4204-33.fits index-4204-34.fits index-4204-35.fits
  index-4204-36.fits index-4204-37.fits index-4204-38.fits index-4204-39.fits
  index-4204-40.fits index-4204-41.fits index-4204-42.fits index-4204-43.fits
  index-4204-44.fits index-4204-45.fits index-4204-46.fits index-4204-47.fits

  # 4205 (12 tiles)
  index-4205-00.fits index-4205-01.fits index-4205-02.fits index-4205-03.fits
  index-4205-04.fits index-4205-05.fits index-4205-06.fits index-4205-07.fits
  index-4205-08.fits index-4205-09.fits index-4205-10.fits index-4205-11.fits

  # 4206 (12 tiles)
  index-4206-00.fits index-4206-01.fits index-4206-02.fits index-4206-03.fits
  index-4206-04.fits index-4206-05.fits index-4206-06.fits index-4206-07.fits
  index-4206-08.fits index-4206-09.fits index-4206-10.fits index-4206-11.fits

  # 4207 (12 tiles)
  index-4207-00.fits index-4207-01.fits index-4207-02.fits index-4207-03.fits
  index-4207-04.fits index-4207-05.fits index-4207-06.fits index-4207-07.fits
  index-4207-08.fits index-4207-09.fits index-4207-10.fits index-4207-11.fits

  # Single-file indexes
  index-4208.fits
  index-4209.fits
  index-4210.fits
  index-4211.fits
  index-4212.fits

  # OPTIONAL: uncomment for insurance
  # index-4213.fits
)

echo "Downloading required astrometry.net index files..."
for f in "${FILES[@]}"; do
    echo "→ $f"
    wget -c "${BASE_URL}/${f}" -O "${OUTDIR}/${f}"
done

echo "Done!"
echo "Indexes stored in $OUTDIR"
