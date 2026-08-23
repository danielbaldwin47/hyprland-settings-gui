"""Rows: the widget-per-type Row factory (ADR-0013).

`factory.py` builds one Row per Option from the Schema's resolved widget -- switch, spinner,
combo, entry, and the four complex-value editors (colour, gradient, css-gaps, vec2).
`state.py` decides everything the Row's chrome shows without a toolkit, `chrome.py` builds
that strip, and `gesture.py` owns the preview-per-tick / one-transaction-on-release shape of
a continuous drag (ADR-0010).
"""
