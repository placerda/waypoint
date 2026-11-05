from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge import minimal_retrieve_request, validate_minimal_mcp_endpoint
from toolbox import assurance_specs


NATIVE = (
    "https://private.search.windows.net/knowledgebases/contracts/mcp"
    "?api-version=2026-04-01"
)


class MinimalFoundryIQTests(unittest.IsolatedAsyncioTestCase):
    def test_ga_rest_request_has_only_supported_intents(self) -> None:
        self.assertEqual(minimal_retrieve_request(" BluePeak process error charges "), {
            "intents": [{"type": "semantic", "search": "BluePeak process error charges"}],
        })
        with self.assertRaises(ValueError):
            minimal_retrieve_request(" ")
        self.assertEqual(validate_minimal_mcp_endpoint(NATIVE), NATIVE)
        for endpoint in (
            NATIVE.replace("2026-04-01", "2026-08-01-preview"),
            NATIVE.replace("/mcp?", "/retrieve?"),
            NATIVE.replace("https:", "http:"),
            NATIVE + "&outputMode=answerSynthesis",
            NATIVE.replace("private.search", "user:secret@private.search"),
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_minimal_mcp_endpoint(endpoint)

    async def test_real_project_toolbox_spec_does_not_invent_mcp_input_schema(self) -> None:
        config = {
            "WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED": "true",
            "WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE": "minimal-extractive",
            "WAYPOINT_KB_MCP_ENDPOINT": NATIVE,
            "TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT":
                "https://agent.example.org/api/projects/demo/toolboxes/contracts/mcp",
            "WAYPOINT_KB_TOOL_NAME": "contracts___knowledge_base_retrieve",
        }
        with patch.dict(os.environ, config), patch(
            "toolbox.toolbox_token", new=AsyncMock(return_value="test-project-token")
        ) as token:
            spec, = await assurance_specs()
            token.assert_awaited_once_with()
            self.assertEqual(spec["server_url"],
                             config["TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT"])
            self.assertNotEqual(spec["server_url"], NATIVE)
            self.assertEqual(spec["allowed_tools"], [config["WAYPOINT_KB_TOOL_NAME"]])
            self.assertEqual(spec["headers"]["Authorization"], "Bearer " + "test-project-token")
            self.assertEqual(spec["require_approval"], "never")
            self.assertNotIn('"query"', str(spec))
            self.assertNotIn('"messages"', str(spec))
            self.assertIn("MCP-discovered", spec["server_description"])
            with patch.dict(os.environ, {"WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE": "synthesis"}):
                with self.assertRaisesRegex(ValueError, "model-free"):
                    await assurance_specs()
            with patch.dict(os.environ, {"WAYPOINT_KB_MCP_ENDPOINT":
                                        NATIVE.replace("2026-04-01", "2026-08-01-preview")}):
                with self.assertRaisesRegex(ValueError, "GA 2026"):
                    await assurance_specs()
