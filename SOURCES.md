# Sources and attribution

- Governorate boundaries: geoBoundaries gbOpen EGY ADM1, boundary ID EGY-ADM1-46179711, represents 2017, 27 units. Underlying sources OpenStreetMap / Wambacher; returned license Open Data Commons Open Database License 1.0 (ODbL). Original downloaded GeoJSON is redistributed unchanged. Attribution: © OpenStreetMap contributors; https://www.openstreetmap.org/copyright . Metadata is preserved in data/boundaries-metadata.json. geoBoundaries API: https://www.geoboundaries.org/api.html . This is not an authoritative contemporary cadastral map.
- SoilGrids: ISRIC, CC BY 4.0; https://isric.org/explore/soilgrids ; https://docs.isric.org/globaldata/soilgrids/wcs.html . REST paused at verification; implementation uses WCS. Values are predictions with uncertainty, not ground truth. sand/clay/silt maps g/kg converted to percent; pH values scaled by 10.
- NASA POWER: https://power.larc.nasa.gov/docs/services/api/temporal/daily/ . Regional gridded meteorological data; fill values excluded.
- Open-Meteo: https://open-meteo.com/ ; https://open-meteo.com/en/docs ; CC BY 4.0 for data, service usage terms apply. Free service availability/rate limits not guaranteed.
- Landsat Collection 2 Level 2: USGS/NASA, accessed through Microsoft Planetary Computer STAC https://planetarycomputer.microsoft.com/api/stac/v1 . Reflectance scaling comes from asset metadata; QA_PIXEL and QA_RADSAT mask invalid samples.
- NASA CMR: https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html . Catalog metadata only, not numeric decoding of mission products.
- FAO irrigation methodology: https://www.fao.org/4/x0490e/x0490e0a.htm . ETc=Kc*ETo under standard conditions.
- Crop rotation reference for future rule review: https://www.sare.org/resources/crop-rotation-on-organic-farms/ . No crop-suitability dataset is inferred from this reference.

No original dataset ownership or NASA endorsement is implied. Refer to individual source licenses for reuse obligations.
