# Achievement packs

Drop a `*.json` file in this folder and it is loaded automatically. The generic
launcher set lives in `data/launcher_achievements.json`; themed packs live here.

Adding a normal achievement **does not require engine changes**. Use an `id`
that is unique across all packs. If a matching icon exists at
`data/achievements/<id>.png`, the UI uses it; otherwise it draws a monogram
fallback.

## Pack shape

```json
{
  "schema": 1,
  "enabled": true,
  "pack": {
    "id": "half_life",
    "title": "Half-Life",
    "description": "Tribute achievements for the Half-Life universe."
  },
  "achievements": [
    {
      "id": "hl2_crowbar",
      "title": "The Right Man in the Wrong Place",
      "desc": "Spend one hour in Half-Life 2.",
      "category": "tribute",
      "icon": "hl2_crowbar",
      "hidden": false,
      "trigger": {
        "stat": "hours_by_title",
        "title": "Half-Life 2",
        "op": ">=",
        "value": 1
      }
    }
  ]
}
```

Set `"enabled": false` to keep a pack in the repo without loading it.

## Trigger examples

```json
{"stat": "hours_total", "op": ">=", "value": 100}
{"stat": "gold_tier_count_current", "op": ">=", "value": 1}
{"stat": "distinct_titles_by_franchise", "franchise": "half_life", "op": ">=", "value": 3}
{"stat": "hours_by_franchise", "list": "quake", "op": ">=", "value": 10}
{"event": "launcher_start", "where": {"os": "linux"}}
{"event": "game_installed", "where": {"local_hour_min": 0, "local_hour_max": 5}}
{"all": [
  {"stat": "hours_total", "op": ">=", "value": 10},
  {"stat": "weekend_hours_ratio", "op": ">=", "value": 0.5}
]}
```

Title lists used by `franchise` / `list` are in
`data/achievement_titles.json`. Add a new list there, then reference it from a
pack. Achievement checks intentionally do **not** consult the future wallet
blacklist: themed achievements can still exist for a franchise even if a
publisher is blacklisted for buy nudges.
