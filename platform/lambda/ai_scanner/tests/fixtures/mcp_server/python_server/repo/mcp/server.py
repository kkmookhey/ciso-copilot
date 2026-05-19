from mcp.server import Server

server = Server("kk-tools")

@server.list_tools()
async def list_tools():
    return [
        {"name": "read_file", "description": "Read a file"},
        {"name": "create_pr",  "description": "Open a pull request"},
    ]
