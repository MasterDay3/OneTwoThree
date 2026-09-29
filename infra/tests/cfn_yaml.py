"""SafeLoader extension for CloudFormation's YAML intrinsic-function tags.

CloudFormation templates use short-form `!`-tags (`!Ref`, `!Sub`, `!GetAtt`,
...) that plain PyYAML does not know how to construct. This module registers
a multi-constructor on a `yaml.SafeLoader` subclass that converts every such
tag into CloudFormation's long form (`{"Ref": ...}`, `{"Fn::Sub": ...}`, ...)
so a template loads as an ordinary Python dict that tests can assert on
directly, with no special-cased tag objects to unwrap.

Supported tags: !Ref, !Sub, !GetAtt (including the dotted short form, e.g.
`!GetAtt Resource.Attribute` -> `{"Fn::GetAtt": ["Resource", "Attribute"]}`),
!If, !Equals, !Not, !And, !Or, !Condition, !Select, !Split, !Join,
!FindInMap, !ImportValue, !Base64, !Cidr, !GetAZs. Any other `!Tag` is
converted generically to `{"Fn::Tag": <value>}` (or `{"Tag": <value>}` for
`Ref`/`Condition`), so an unlisted intrinsic still loads instead of raising.
"""

import yaml

# These tags map to `{tag: value}`, not `{"Fn::" + tag: value}`.
_BARE_TAGS = {"Ref", "Condition"}


class CfnLoader(yaml.SafeLoader):
    """A SafeLoader that understands CloudFormation's `!`-tag shorthand."""


def _construct_intrinsic(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        if tag_suffix == "GetAtt" and "." in value:
            value = value.split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:  # pragma: no cover - PyYAML nodes are always one of the above
        raise yaml.constructor.ConstructorError(
            None, None, f"unexpected node for !{tag_suffix}", node.start_mark
        )

    key = tag_suffix if tag_suffix in _BARE_TAGS else f"Fn::{tag_suffix}"
    return {key: value}


CfnLoader.add_multi_constructor("!", _construct_intrinsic)


def load(stream):
    """Parse a CloudFormation template from a file-like object or string."""
    return yaml.load(stream, Loader=CfnLoader)


def load_path(path):
    """Parse a CloudFormation template from a filesystem path."""
    with open(path, encoding="utf-8") as fh:
        return load(fh)
