"""Rows: the widget-per-type Row factory (ADR-0013).

`factory.py` builds one Row per Option from the Schema's resolved widget -- switch, spinner,
combo and entry so far. The rest of the Row contract (state pills, Value summary, Dependency
badge, per-row reset, ⓘ Help popover) is #57, and the complex-value editors are #58.
"""
