#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================================="
echo "   🚀 Starting OpenThai 2.0 OCR Studio (Local Engine)    "
echo "=========================================================="
echo " • Engine: OpenThai 2.0 OCR Text (ONNX CPU, 4.2 MB)"
echo " • URL:    http://127.0.0.1:8765"
echo " • Status: 100% Offline & Private (Zero Data Sent to Cloud)"
echo "=========================================================="

# Launch browser after a short delay in background
(sleep 1.5 && open "http://127.0.0.1:8765") &

# Run server with Python 3.11 virtualenv
exec "$DIR/.venv/bin/python3" "$DIR/app.py"
