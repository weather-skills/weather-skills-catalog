"""Plotly figures from weather-skills Zarrs: spec → render → lint → bind → export.

The spec is Jsonnet that evaluates to a standard Plotly figure. Where Plotly
expects data, a string starting with ``@`` names it: ``"@OBS.precip.isel(time=0)"``.

=====================  ========================================================
Module                 Step
=====================  ========================================================
:mod:`.render`         Evaluate the Jsonnet with the inputs' metadata
:mod:`.lint`           Every data key holds an ``@`` binding (Plotly's schema)
:mod:`.bind`           Resolve ``@`` references, Natural Earth and GeoJSON lines
:mod:`.export`         Plotly's checks, the file, and the QA lines
``lib/``               ``ws.libsonnet`` helpers and palettes
=====================  ========================================================
"""
