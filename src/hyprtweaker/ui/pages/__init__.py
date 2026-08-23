"""Pages: the generated Page factory and the curated Tasks mapping.

Two halves, split so the interesting one needs no display:

- `plan.py` -- which Options a Section's Page shows, in which Groups, in what order. Pure
  data derived from the Schema, and toolkit-free.
- `config.py` -- the `Adw.PreferencesPage` built from one of those plans.

The curated Tasks mapping is #71; it is a different `groups` list over the same Schema,
which is the evidence ADR-0013 wanted for "one Schema, two Views".
"""
