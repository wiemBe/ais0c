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
Input Schema Builder

Provides a fluent interface for building JSON Schema definitions for tool inputs.
Makes it easy to define schemas without error-prone manual JSON construction.
"""

from typing import Dict, Any, List, Optional


class PropertyBuilder:
    """Builder for individual property definitions."""

    def __init__(self, name: str, prop_type: str):
        self.name = name
        self._schema: Dict[str, Any] = {"type": prop_type}

    def description(self, desc: str) -> 'PropertyBuilder':
        """Set the property description."""
        self._schema["description"] = desc
        return self

    def minimum(self, value: int | float) -> 'PropertyBuilder':
        """Set minimum value (for numbers)."""
        self._schema["minimum"] = value
        return self

    def maximum(self, value: int | float) -> 'PropertyBuilder':
        """Set maximum value (for numbers)."""
        self._schema["maximum"] = value
        return self

    def min_length(self, value: int) -> 'PropertyBuilder':
        """Set minimum length (for strings)."""
        self._schema["minLength"] = value
        return self

    def max_length(self, value: int) -> 'PropertyBuilder':
        """Set maximum length (for strings)."""
        self._schema["maxLength"] = value
        return self

    def pattern(self, regex: str) -> 'PropertyBuilder':
        """Set regex pattern (for strings)."""
        self._schema["pattern"] = regex
        return self

    def enum(self, values: List[Any]) -> 'PropertyBuilder':
        """Set allowed values."""
        self._schema["enum"] = values
        return self

    def items(self, item_type: str) -> 'PropertyBuilder':
        """Set array item type."""
        self._schema["items"] = {"type": item_type}
        return self

    def default(self, value: Any) -> 'PropertyBuilder':
        """Set default value."""
        self._schema["default"] = value
        return self

    def build(self) -> Dict[str, Any]:
        """Build the property schema."""
        return self._schema


class SchemaBuilder:
    """
    Fluent builder for JSON Schema definitions.

    Example:
        schema = (SchemaBuilder()
            .string("name")
                .description("User's name")
                .min_length(1)
                .required()
            .integer("age")
                .description("User's age")
                .minimum(0)
                .maximum(150)
            .boolean("active")
                .description("Whether user is active")
                .default(True)
            .build())
    """

    def __init__(self):
        self._properties: Dict[str, Dict[str, Any]] = {}
        self._required: List[str] = []
        self._current_property: Optional[PropertyBuilder] = None

    def _finalize_current(self) -> 'SchemaBuilder':
        """Finalize the current property being built."""
        if self._current_property:
            self._properties[self._current_property.name] = self._current_property.build()
            self._current_property = None
        return self

    def string(self, name: str) -> 'SchemaBuilder':
        """Add a string property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "string")
        return self

    def integer(self, name: str) -> 'SchemaBuilder':
        """Add an integer property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "integer")
        return self

    def number(self, name: str) -> 'SchemaBuilder':
        """Add a number (float) property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "number")
        return self

    def boolean(self, name: str) -> 'SchemaBuilder':
        """Add a boolean property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "boolean")
        return self

    def array(self, name: str) -> 'SchemaBuilder':
        """Add an array property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "array")
        return self

    def object(self, name: str) -> 'SchemaBuilder':
        """Add an object property."""
        self._finalize_current()
        self._current_property = PropertyBuilder(name, "object")
        return self

    def description(self, desc: str) -> 'SchemaBuilder':
        """Set description for current property."""
        if self._current_property:
            self._current_property.description(desc)
        return self

    def minimum(self, value: int | float) -> 'SchemaBuilder':
        """Set minimum value for current property."""
        if self._current_property:
            self._current_property.minimum(value)
        return self

    def maximum(self, value: int | float) -> 'SchemaBuilder':
        """Set maximum value for current property."""
        if self._current_property:
            self._current_property.maximum(value)
        return self

    def min_length(self, value: int) -> 'SchemaBuilder':
        """Set minimum length for current property."""
        if self._current_property:
            self._current_property.min_length(value)
        return self

    def max_length(self, value: int) -> 'SchemaBuilder':
        """Set maximum length for current property."""
        if self._current_property:
            self._current_property.max_length(value)
        return self

    def pattern(self, regex: str) -> 'SchemaBuilder':
        """Set regex pattern for current property."""
        if self._current_property:
            self._current_property.pattern(regex)
        return self

    def enum(self, values: List[Any]) -> 'SchemaBuilder':
        """Set allowed values for current property."""
        if self._current_property:
            self._current_property.enum(values)
        return self

    def items(self, item_type: str) -> 'SchemaBuilder':
        """Set array item type for current property."""
        if self._current_property:
            self._current_property.items(item_type)
        return self

    def default(self, value: Any) -> 'SchemaBuilder':
        """Set default value for current property."""
        if self._current_property:
            self._current_property.default(value)
        return self

    def required(self) -> 'SchemaBuilder':
        """Mark current property as required."""
        if self._current_property:
            self._required.append(self._current_property.name)
        return self

    def build(self) -> Dict[str, Any]:
        """Build the complete schema."""
        self._finalize_current()

        result_schema: Dict[str, Any] = {
            "type": "object",
            "properties": self._properties
        }

        if self._required:
            result_schema["required"] = self._required

        return result_schema


# Convenience function for creating schemas
def schema() -> SchemaBuilder:
    """Create a new schema builder."""
    return SchemaBuilder()
