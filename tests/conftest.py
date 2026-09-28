"""Test safety net: the offline suite must never touch a real CATIA.

* CATIA_MCP_OFFLINE=1 makes CATIAConnection.connect() refuse to attach to or launch CATIA.
* CATIA_MCP_HOME points to a throw-away directory: no real logs, lessons or locks are touched.
"""

import os
import tempfile

os.environ["CATIA_MCP_OFFLINE"] = "1"
os.environ.setdefault("CATIA_MCP_HOME", tempfile.mkdtemp(prefix="catia_mcp_tests_"))
