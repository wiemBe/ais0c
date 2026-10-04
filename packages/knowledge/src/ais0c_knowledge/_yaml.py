"""YAML loading for knowledge files: the safe loader, with duplicate keys rejected.

PyYAML keeps the last of two equal keys, so a manifest could silently override a field set
earlier in the same file.
"""

from collections.abc import Hashable
from typing import Any

import yaml


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Hashable, Any]:
    seen: set[object] = set()
    for key_node, _value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            continue  # unhashable keys are rejected by construct_mapping
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml_text(text: str) -> object:
    """Parse one YAML document. Raises yaml.YAMLError on invalid YAML or duplicate keys."""
    return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
