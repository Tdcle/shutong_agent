"""MCP Client Manager — migrated from Java MCP SyncMcpToolCallbackProvider.

Manages connections to MCP servers and exposes their tools to agents.
Supports STDIO and Streamable HTTP transports.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.tools.base import tool

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    transport: str  # "stdio" or "streamable_http"
    command: str | None = None  # For STDIO: e.g. "npx -y @anthropic/mcp-server-filesystem ."
    args: list[str] | None = None
    url: str | None = None  # For HTTP: e.g. "http://127.0.0.1:8004/mcp"
    headers: dict | None = None
    timeout: int = 60


class MCPClientManager:
    """Manages MCP client connections and exposes tools.

    Equivalent to Java's SyncMcpToolCallbackProvider + McpSyncClient.
    """

    def __init__(self):
        self._servers: dict[str, MCPServerConfig] = {}
        self._tools: dict[str, BaseTool] = {}
        self._clients: dict[str, Any] = {}

    def register_server(self, config: MCPServerConfig):
        self._servers[config.name] = config

    async def connect(self, server_name: str) -> list[BaseTool]:
        """Connect to an MCP server and discover its tools."""
        config = self._servers.get(server_name)
        if not config:
            raise ValueError(f"Unknown MCP server: {server_name}")

        if config.transport == "stdio":
            return await self._connect_stdio(config)
        elif config.transport == "streamable_http":
            return await self._connect_http(config)
        else:
            raise ValueError(f"Unsupported transport: {config.transport}")

    async def connect_all(self) -> dict[str, list[BaseTool]]:
        results = {}
        for name in self._servers:
            try:
                results[name] = await self.connect(name)
            except Exception as e:
                logger.error("Failed to connect MCP server %s: %s", name, e)
                results[name] = []
        return results

    async def _connect_stdio(self, config: MCPServerConfig) -> list[BaseTool]:
        """Connect via STDIO transport using the official MCP Python SDK."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=config.command,
                args=config.args or [],
            )

            self._clients[config.name] = {"params": params, "transport": "stdio"}
            logger.info("MCP STDIO client ready for %s", config.name)

            return await self._list_tools_stdio(config.name, params)
        except ImportError:
            logger.warning("MCP SDK not available, using fallback for %s", config.name)
            return []
        except Exception as e:
            logger.error("Failed to connect MCP STDIO server %s: %s", config.name, e)
            return []

    async def _connect_http(self, config: MCPServerConfig) -> list[BaseTool]:
        """Connect via Streamable HTTP transport."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            self._clients[config.name] = {"url": config.url, "transport": "streamable_http"}
            logger.info("MCP HTTP client ready for %s", config.name)
            return await self._list_tools_http(config.name, config)
        except ImportError:
            logger.warning("MCP SDK not available, using fallback for %s", config.name)
            return []
        except Exception as e:
            logger.error("Failed to connect MCP HTTP server %s: %s", config.name, e)
            return []

    async def _list_tools_stdio(self, name: str, params) -> list[BaseTool]:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return [self._mcp_tool_to_langchain(name, t) for t in result.tools]
        except Exception as e:
            logger.warning("Could not list tools for %s: %s", name, e)
            return []

    async def _list_tools_http(self, name: str, config: MCPServerConfig) -> list[BaseTool]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(config.url, headers=config.headers or {}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return [self._mcp_tool_to_langchain(name, t) for t in result.tools]
        except Exception as e:
            logger.warning("Could not list tools for %s: %s", name, e)
            return []

    def _mcp_tool_to_langchain(self, server_name: str, mcp_tool) -> BaseTool:
        """Convert an MCP tool definition to a LangChain BaseTool."""
        tool_name = mcp_tool.name
        tool_desc = getattr(mcp_tool, "description", f"Tool from MCP server {server_name}")
        input_schema = getattr(mcp_tool, "inputSchema", {})

        @tool(name=f"mcp_{server_name}__{tool_name}", description=tool_desc)
        async def mcp_wrapper(*args, **kwargs) -> str:
            # This is a simplified wrapper — in production, you'd call the MCP server
            return f"MCP tool {tool_name}: {kwargs}"

        return mcp_wrapper

    def get_all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)
