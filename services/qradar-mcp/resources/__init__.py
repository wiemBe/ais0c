# Copyright 2026 IBM Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
MCP Resources Module

This module exports all MCP resource classes. Resources are now registered via the FastMCP
server in server.py using the @mcp.resource() decorator.

To add a new resource:
1. Create a new class that inherits from MCPResource
2. Implement the required methods (uri, name, description, mime_type, read)
3. Import it in this file
4. Register it in server.py using @mcp.resource() decorator
"""

# Import base classes
from .base import MCPResource

# Import and register all resources
from .aql_fields import AQLEventsFieldsResource, AQLFlowsFieldsResource
from .aql_functions import AQLFunctionsResource
from .aql_guide import AQLGenerationGuideResource



# Export public API
__all__ = [
    'MCPResource',
    'AQLEventsFieldsResource',
    'AQLFlowsFieldsResource',
    'AQLFunctionsResource',
    'AQLGenerationGuideResource',
]
