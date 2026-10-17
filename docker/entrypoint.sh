#!/bin/sh
set -e

DATA_DIR="${DATA_DIR:-/app/data/raw/challenge}"
if [ ! -d "$DATA_DIR/sample-data" ]; then
  echo "Fetching the challenge dataset..."
  git clone --depth 1 https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset "$DATA_DIR"
fi

until python -c "import os, sqlalchemy as sa; sa.create_engine(os.environ['DATABASE_URL']).connect().close()" 2>/dev/null; do
  echo "Waiting for the database..."
  sleep 2
done

wellwell init "$DATA_DIR/${DATA_FOLDER:-sample-data}"
wellwell embed
exec wellwell serve --host 0.0.0.0 --port 8765
