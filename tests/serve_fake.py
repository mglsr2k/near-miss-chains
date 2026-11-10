"""Serve the app with the scripted stand-in model, to look at the page without Vertex credentials."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import uvicorn

import app
from test_flow import install

install()
uvicorn.run(app.app, host="127.0.0.1", port=8011, log_level="warning")
