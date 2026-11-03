# Mapping files

- `roster.json` — column mapping for `daybook roster import <csv> --map mappings/roster.json --class <class>`.
  `columns` maps a CSV header to a DAYBOOK field: `full_name` (required), `ext_id`, `active`, `parent_phone`.
  `active_values` lists the `Status` values that mean "active" (others -> inactive). Columns not listed are dropped.
  Re-importing is safe: rows match existing students by `ext_id`, else by an unambiguous `full_name`.
- `roster.example.csv` — synthetic example in the teacher's export format.
- `tests.example.csv` — synthetic example for the generic `daybook import tests <csv>`.
- Screener rounds need no mapping file: `daybook import screeners <dir>` reads
  `round_<set_id>_<YYYY-MM-DD>.json` (see `demo/screeners/` and HANDOFF.md).
