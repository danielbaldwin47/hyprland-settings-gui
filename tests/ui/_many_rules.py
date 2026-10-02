"""A 332-rule rice for the Rules page's chip overflow (#113, review of #151 finding 1).

Generated, not a corpus rice: the corpus's largest window-rule set is jakoolit's, whose
three rule files hold 332 `windowrule` lines between them but import as 231 rules at most,
using 24 chips. The ticket's case is the worst one a rice can reach, about 75 chips, so
rule `i` names match prop `i mod 17` and effect `i mod 57` of the window catalog: every
one of the catalog's 17 match props and 57 effects is in use, 74 chips in all. Not a test
module: the leading underscore keeps pytest from collecting it.
"""

from __future__ import annotations

from typing import Any

RULE_COUNT = 332

_MATCH_VALUES = {"regex": "app-{i}", "bool": True, "int": 1, "workspace": "1", "tag": "tag-{i}"}
_EFFECT_VALUES = {"bool": True, "int": 1, "float": 0.9, "string": "1"}


def many_rules() -> list[Any]:
    """`RULE_COUNT` window rules covering every catalog match prop and effect."""
    from hyprtweaker.engine.model.entities import WindowRule
    from hyprtweaker.engine.rules_catalog import effects, match_props

    props, catalog_effects = match_props("window"), effects("window")
    rules = []
    for i in range(RULE_COUNT):
        prop = props[i % len(props)]
        effect = catalog_effects[i % len(catalog_effects)]
        match_value = _MATCH_VALUES[prop.kind.value]
        rules.append(
            WindowRule(
                match={
                    prop.name: match_value.format(i=i)
                    if isinstance(match_value, str)
                    else match_value
                },
                effects={effect.name: _EFFECT_VALUES[effect.type.value]},
            )
        )
    return rules
