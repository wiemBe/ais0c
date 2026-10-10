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
Feature Toggle Manager

Manages feature toggle state and evaluation for the three-level toggle system:
1. HTTP Verb Level - Global control across ALL tools
2. Tool Group Level - Control by directory/functional group
3. Individual Tool Level - Per-tool overrides in JSON config
"""

import json
import os
from typing import Dict, List, Any, Optional

from qradar_mcp.utils.structured_logger import log_structured


class FeatureToggleConfigError(Exception):
    """Raised when feature toggle configuration is invalid or missing"""


class FeatureToggleManager:
    """
    Manages feature toggle state and evaluation.

    Toggle Resolution Logic:
    1. Check per_tool_toggles[tool_class_name] (from JSON config)
       - If True: ALLOW (always enabled)
       - If False: DENY (always disabled)
       - If not present: Continue to step 2

    2. Check verb_toggles[verb] AND group_toggles[group]
        - If BOTH are true: ALLOW
        - If EITHER is false: DENY
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize with configuration from feature_toggles.json

        Args:
            config_path: Path to feature_toggles.json file.
                        If None, looks for it in the qradar-mcp directory.

        Raises:
            FeatureToggleConfigError: If config file doesn't exist or is invalid
        """
        if config_path is None:
            # Default to qradar-mcp/feature_toggles.json
            base_dir = os.path.dirname(os.path.dirname(__file__))
            config_path = os.path.join(base_dir, 'feature_toggles.json')

        self.config_path = config_path
        self._load_config()

    def _load_config(self):
        """
        Load configuration from feature_toggles.json

        Raises:
            FeatureToggleConfigError: If file doesn't exist or is invalid
        """
        if not os.path.exists(self.config_path):
            raise FeatureToggleConfigError(
                f"Feature toggle configuration file not found: {self.config_path}. "
                "Server cannot start without this file."
            )

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise FeatureToggleConfigError(
                f"Invalid JSON in feature toggle configuration: {e}"
            ) from e

        # Validate required fields
        if 'verb_toggles' not in config:
            raise FeatureToggleConfigError(
                "Missing required field 'verb_toggles' in feature toggle configuration"
            )
        if 'group_toggles' not in config:
            raise FeatureToggleConfigError(
                "Missing required field 'group_toggles' in feature toggle configuration"
            )

        self.verb_toggles = config['verb_toggles']
        self.group_toggles = config['group_toggles']
        self.per_tool_toggles = config.get('per_tool_toggles', {})
        self.resource_toggles = config.get('resource_toggles', {})

        log_structured(
            "Feature toggle configuration loaded successfully",
            level='INFO',
            config_path=self.config_path,
            verb_toggles=self.verb_toggles,
            group_toggles=self.group_toggles,
            per_tool_toggle_count=len(self.per_tool_toggles),
            resource_toggle_count=len(self.resource_toggles)
        )

    def is_tool_enabled(self, tool) -> bool:
        """
        Evaluate if a tool is enabled based on toggle hierarchy.

        Resolution logic:
        1. Check per_tool_toggles[tool_class_name] (from JSON config)
           - If True: ALLOW (always enabled)
           - If False: DENY (always disabled)
           - If None or not present: Continue to step 2

        2. Check verb_toggles[verb] AND group_toggles[group]
           - If BOTH are true: ALLOW
           - If EITHER is false: DENY

        Args:
            tool: MCPTool instance

        Returns:
            bool: True if tool is enabled, False otherwise
        """
        # Step 1: Check per-tool override (from JSON config)
        tool_class_name = tool.__class__.__name__
        if tool_class_name in self.per_tool_toggles:
            override_value = self.per_tool_toggles[tool_class_name]
            # Only use override if it's explicitly True or False (not None)
            if override_value is not None:
                return override_value

        # Step 2: Check verb AND group toggles (both must be true)
        verb = tool.http_verb
        group = tool.tool_group

        verb_enabled = self.verb_toggles.get(verb, False)
        group_enabled = self.group_toggles.get(group, False)

        # Both must be true for tool to be enabled
        return verb_enabled and group_enabled

    def get_tool_state_summary(self, tools: List[Any]) -> Dict[str, Any]:
        """
        Get summary of all tool states for logging

        Args:
            tools: List of MCPTool instances

        Returns:
            dict: Summary of tool states including counts and details
        """
        enabled_tools = []
        disabled_tools = []

        for tool in tools:
            is_enabled = self.is_tool_enabled(tool)
            tool_class_name = tool.__class__.__name__
            has_override = tool_class_name in self.per_tool_toggles

            tool_info = {
                'name': tool.name,
                'class_name': tool_class_name,
                'group': tool.tool_group,
                'verb': tool.http_verb,
                'has_override': has_override,
                'enabled': is_enabled
            }

            if is_enabled:
                enabled_tools.append(tool_info)
            else:
                disabled_tools.append(tool_info)

        return {
            'total': len(tools),
            'enabled': len(enabled_tools),
            'disabled': len(disabled_tools),
            'enabled_tools': enabled_tools,
            'disabled_tools': disabled_tools
        }


# Global feature toggle manager instance
FEATURE_TOGGLE_MANAGER: Optional[FeatureToggleManager] = None


def get_feature_toggle_manager() -> Optional[FeatureToggleManager]:
    """
    Get the global feature toggle manager instance.

    Returns:
        FeatureToggleManager: The global feature toggle manager, or None if not set
    """
    return FEATURE_TOGGLE_MANAGER


def set_feature_toggle_manager(manager: Optional[FeatureToggleManager]):
    """
    Set the global feature toggle manager instance.

    Args:
        manager: The FeatureToggleManager instance to set as global, or None to clear
    """
    global FEATURE_TOGGLE_MANAGER  # pylint: disable=global-statement
    FEATURE_TOGGLE_MANAGER = manager
