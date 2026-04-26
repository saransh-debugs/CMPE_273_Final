#!/usr/bin/env bash
# Generates Python stubs from proto/tracing.proto.
# Run from repo root: bash scripts/compile_proto.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p generated
touch generated/__init__.py

python -m grpc_tools.protoc \
    -I proto \
    --python_out=generated \
    --grpc_python_out=generated \
    --pyi_out=generated \
    proto/tracing.proto

# grpc_tools generates absolute imports like `import tracing_pb2` — patch it
# so the file works as a package import (`from generated import tracing_pb2`).
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' 's/^import tracing_pb2/from . import tracing_pb2/' generated/tracing_pb2_grpc.py
else
    sed -i 's/^import tracing_pb2/from . import tracing_pb2/' generated/tracing_pb2_grpc.py
fi

echo "✅ Proto compiled to generated/"
