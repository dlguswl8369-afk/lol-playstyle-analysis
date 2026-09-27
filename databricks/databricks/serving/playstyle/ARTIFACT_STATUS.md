# Playstyle artifact status

`style_model.json` is intentionally absent. The supplied archives contain the
training notebook and serving code, but not a trained artifact or the historical
Delta data required to reproduce one.

Generate it in Databricks by running
`databricks/offline/playstyle/04_ml_training_v8.py` after its prerequisite
Silver data exists. The notebook is configured with `ANALYSIS_GAMES = 10` and
exports top-level and config metadata for `analysis_games=10` and
`queue_filter=420`.

Do not create a placeholder JSON. Serving fails explicitly while the trained
artifact is missing or incompatible.
