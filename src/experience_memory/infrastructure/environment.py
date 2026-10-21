from importlib.metadata import PackageNotFoundError, version
import platform
import sys

REQUIRED_MCP_VERSION = "2.3.0"


def inspect_environment():
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        installed = None
    python_supported = sys.version_info >= (3, 10)
    compatible = installed == REQUIRED_MCP_VERSION
    return {
        "python_version": platform.python_version(),
        "python_supported": python_supported,
        "mcp_required": REQUIRED_MCP_VERSION,
        "mcp_installed": installed,
        "mcp_compatible": compatible,
        "ready": python_supported and compatible,
    }
