#!/usr/bin/env bash
# package_lambda.sh
#
# Builds the Lambda deployment zip: handler.py + cost_engine/ + the two
# dependencies not present in the default Lambda Python 3.12 runtime
# (boto3 IS present by default - anthropic and python-dotenv are not).
#
# Run from the lambda/ directory.
#
# If your system's Python command isn't "python3" (e.g. Windows with the
# py launcher, where it's often "py312" or "py -3.12"), override it:
#   PYTHON_BIN=py312 ./package_lambda.sh

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
BUILD_DIR="build"
PACKAGE_DIR="${BUILD_DIR}/package"
ZIP_NAME="cost_detective_reactive.zip"

rm -rf "${BUILD_DIR}"
mkdir -p "${PACKAGE_DIR}"

echo "Installing anthropic + python-dotenv into package dir (boto3 is already in the Lambda runtime)..."
echo "Forcing manylinux2014_x86_64 / cp312 wheels regardless of host OS - anthropic's"
echo "dependency on pydantic pulls in pydantic_core, a compiled binary extension."
echo "Installing it via the host's native pip (e.g. on Windows) would silently grab"
echo "Windows-compiled binaries that fail to import on Lambda's Amazon Linux runtime"
echo "with 'No module named pydantic_core._pydantic_core' - this only surfaces at"
echo "actual Lambda invocation time, not at package-build time, which is why it's"
echo "easy to miss until the first real trigger."
pip install anthropic python-dotenv \
  --target "${PACKAGE_DIR}" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --upgrade \
  --quiet

echo "Copying handler.py..."
cp handler.py "${PACKAGE_DIR}/"

echo "Copying cost_engine/*.py flat into package root (analyzer.py, cost_fetcher.py, triage.py) -
cost_engine has no __init__.py and is imported flat everywhere else in this project
(handler.py does 'from analyzer import ...', not 'from cost_engine.analyzer import ...') -
copying it as a nested subfolder would break that import in Lambda."
cp ../cost_engine/*.py "${PACKAGE_DIR}/"

echo "Zipping (via Python's zipfile module - avoids depending on a zip CLI, which Windows Git Bash doesn't ship with)..."
"${PYTHON_BIN}" - "${PACKAGE_DIR}" "${BUILD_DIR}/${ZIP_NAME}" <<'PYEOF'
import os
import sys
import zipfile

package_dir, zip_path = sys.argv[1], sys.argv[2]

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for file in files:
            if file.endswith(".pyc"):
                continue
            full_path = os.path.join(root, file)
            arcname = os.path.relpath(full_path, package_dir)
            zf.write(full_path, arcname)

print(f"Wrote {zip_path} ({os.path.getsize(zip_path)} bytes)")
PYEOF

echo "Done: ${BUILD_DIR}/${ZIP_NAME}"
echo "Terraform expects this at: ../lambda/build/${ZIP_NAME} (relative to terraform/ dir) - matches variables.tf default."
