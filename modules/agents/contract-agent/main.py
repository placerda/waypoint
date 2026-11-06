"""Bounded Waypoint invoice investigation over the Foundry Responses protocol."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from castia import Agent, Depends, Model
from castia.inference.tools import Tool
from dotenv import load_dotenv

from gateway import configured_gateway_model
from toolbox import assurance_tools
from tools import read_tools
from write_tools import write_tools

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logging.getLogger("toolbox").setLevel(logging.INFO)
# Exporter failures must remain visible without recursively exporting themselves.
for exporter_name in (
    "azure.monitor.opentelemetry.exporter",
    "microsoft.opentelemetry.a365.core.exporters",
):
    exporter_logger = logging.getLogger(exporter_name)
    exporter_logger.propagate = False
    exporter_logger.addHandler(logging.StreamHandler())

app = Agent(name="contract-agent")
_instructions = (
    Path(__file__).resolve().parent / ".agent_configs" / "baseline" / "instructions.md"
).read_text(encoding="utf-8")


def chat_model() -> Model:
    return configured_gateway_model(_instructions)


_CHAT_MODEL_DEPENDENCY = Depends(chat_model)
app.tools(read_tools, write_tools)
_ASSURANCE_TOOLS: list[Tool | dict[str, Any]] = [*read_tools(), *write_tools()]


@app.responses()
async def assurance(text: str, model: Model = _CHAT_MODEL_DEPENDENCY) -> str:
    """The sole write tool records assurance; it never executes a payment."""
    return await model.respond_with_tools(
        text,
        tools=[*_ASSURANCE_TOOLS, *await assurance_tools()],
        activity=None,
        max_iterations=8,
    )


if __name__ == "__main__":
    app.run()
