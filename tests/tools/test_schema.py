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
Unit tests for the schema builder module.
"""

from qradar_mcp.tools.schema import SchemaBuilder, PropertyBuilder, schema


class TestPropertyBuilder:
    """Tests for PropertyBuilder class."""

    def test_create_string_property(self):
        """Test creating a string property."""
        prop = PropertyBuilder("name", "string")
        result = prop.build()
        assert result == {"type": "string"}

    def test_create_integer_property(self):
        """Test creating an integer property."""
        prop = PropertyBuilder("age", "integer")
        result = prop.build()
        assert result == {"type": "integer"}

    def test_property_with_description(self):
        """Test adding description to property."""
        prop = PropertyBuilder("name", "string")
        prop.description("User's name")
        result = prop.build()
        assert result == {"type": "string", "description": "User's name"}

    def test_property_with_minimum(self):
        """Test adding minimum constraint."""
        prop = PropertyBuilder("age", "integer")
        prop.minimum(0)
        result = prop.build()
        assert result == {"type": "integer", "minimum": 0}

    def test_property_with_maximum(self):
        """Test adding maximum constraint."""
        prop = PropertyBuilder("age", "integer")
        prop.maximum(150)
        result = prop.build()
        assert result == {"type": "integer", "maximum": 150}

    def test_property_with_min_length(self):
        """Test adding minLength constraint."""
        prop = PropertyBuilder("name", "string")
        prop.min_length(1)
        result = prop.build()
        assert result == {"type": "string", "minLength": 1}

    def test_property_with_max_length(self):
        """Test adding maxLength constraint."""
        prop = PropertyBuilder("name", "string")
        prop.max_length(100)
        result = prop.build()
        assert result == {"type": "string", "maxLength": 100}

    def test_property_with_pattern(self):
        """Test adding pattern constraint."""
        prop = PropertyBuilder("email", "string")
        prop.pattern(r"^[\w\.-]+@[\w\.-]+\.\w+$")
        result = prop.build()
        assert result == {"type": "string", "pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"}

    def test_property_with_enum(self):
        """Test adding enum constraint."""
        prop = PropertyBuilder("status", "string")
        prop.enum(["active", "inactive", "pending"])
        result = prop.build()
        assert result == {"type": "string", "enum": ["active", "inactive", "pending"]}

    def test_property_with_items(self):
        """Test adding items for array type."""
        prop = PropertyBuilder("tags", "array")
        prop.items("string")
        result = prop.build()
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_property_with_default(self):
        """Test adding default value."""
        prop = PropertyBuilder("active", "boolean")
        prop.default(True)
        result = prop.build()
        assert result == {"type": "boolean", "default": True}

    def test_property_chaining(self):
        """Test method chaining."""
        prop = PropertyBuilder("age", "integer")
        result = (prop
                  .description("User's age")
                  .minimum(0)
                  .maximum(150)
                  .default(25)
                  .build())
        assert result == {
            "type": "integer",
            "description": "User's age",
            "minimum": 0,
            "maximum": 150,
            "default": 25
        }


class TestSchemaBuilder:
    """Tests for SchemaBuilder class."""

    def test_empty_schema(self):
        """Test building an empty schema."""
        builder = SchemaBuilder()
        result = builder.build()
        assert result == {"type": "object", "properties": {}}

    def test_single_string_property(self):
        """Test schema with single string property."""
        builder = SchemaBuilder()
        result = (builder
                  .string("name")
                  .description("User's name")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User's name"}
            }
        }

    def test_single_integer_property(self):
        """Test schema with single integer property."""
        builder = SchemaBuilder()
        result = (builder
                  .integer("age")
                  .description("User's age")
                  .minimum(0)
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "age": {"type": "integer", "description": "User's age", "minimum": 0}
            }
        }

    def test_multiple_properties(self):
        """Test schema with multiple properties."""
        builder = SchemaBuilder()
        result = (builder
                  .string("name")
                  .description("User's name")
                  .integer("age")
                  .description("User's age")
                  .minimum(0)
                  .boolean("active")
                  .description("Whether user is active")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User's name"},
                "age": {"type": "integer", "description": "User's age", "minimum": 0},
                "active": {"type": "boolean", "description": "Whether user is active"}
            }
        }

    def test_required_properties(self):
        """Test marking properties as required."""
        builder = SchemaBuilder()
        result = (builder
                  .string("name")
                  .description("User's name")
                  .required()
                  .integer("age")
                  .description("User's age")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User's name"},
                "age": {"type": "integer", "description": "User's age"}
            },
            "required": ["name"]
        }

    def test_multiple_required_properties(self):
        """Test multiple required properties."""
        builder = SchemaBuilder()
        result = (builder
                  .string("name")
                  .required()
                  .string("email")
                  .required()
                  .integer("age")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "age": {"type": "integer"}
            },
            "required": ["name", "email"]
        }

    def test_number_property(self):
        """Test number (float) property."""
        builder = SchemaBuilder()
        result = (builder
                  .number("price")
                  .description("Product price")
                  .minimum(0.0)
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "Product price", "minimum": 0.0}
            }
        }

    def test_array_property(self):
        """Test array property."""
        builder = SchemaBuilder()
        result = (builder
                  .array("tags")
                  .description("List of tags")
                  .items("string")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "description": "List of tags", "items": {"type": "string"}}
            }
        }

    def test_object_property(self):
        """Test object property."""
        builder = SchemaBuilder()
        result = (builder
                  .object("metadata")
                  .description("Additional metadata")
                  .build())
        assert result == {
            "type": "object",
            "properties": {
                "metadata": {"type": "object", "description": "Additional metadata"}
            }
        }

    def test_complex_schema(self):
        """Test complex schema with various property types and constraints."""
        builder = SchemaBuilder()
        result = (builder
                  .string("name")
                  .description("User's name")
                  .min_length(1)
                  .max_length(100)
                  .required()
                  .integer("age")
                  .description("User's age")
                  .minimum(0)
                  .maximum(150)
                  .boolean("active")
                  .description("Whether user is active")
                  .default(True)
                  .array("tags")
                  .description("User tags")
                  .items("string")
                  .string("status")
                  .description("User status")
                  .enum(["active", "inactive", "pending"])
                  .required()
                  .build())

        assert result == {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "User's name",
                    "minLength": 1,
                    "maxLength": 100
                },
                "age": {
                    "type": "integer",
                    "description": "User's age",
                    "minimum": 0,
                    "maximum": 150
                },
                "active": {
                    "type": "boolean",
                    "description": "Whether user is active",
                    "default": True
                },
                "tags": {
                    "type": "array",
                    "description": "User tags",
                    "items": {"type": "string"}
                },
                "status": {
                    "type": "string",
                    "description": "User status",
                    "enum": ["active", "inactive", "pending"]
                }
            },
            "required": ["name", "status"]
        }


class TestSchemaFunction:
    """Tests for the schema() convenience function."""

    def test_schema_function_returns_builder(self):
        """Test that schema() returns a SchemaBuilder instance."""
        builder = schema()
        assert isinstance(builder, SchemaBuilder)

    def test_schema_function_usage(self):
        """Test using schema() function to build a schema."""
        result = (schema()
                  .string("name")
                  .description("User's name")
                  .required()
                  .integer("age")
                  .description("User's age")
                  .minimum(0)
                  .build())

        assert result == {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User's name"},
                "age": {"type": "integer", "description": "User's age", "minimum": 0}
            },
            "required": ["name"]
        }
