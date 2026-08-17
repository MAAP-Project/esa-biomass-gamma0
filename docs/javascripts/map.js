(() => {
  const mapElement = document.querySelector("#gamma0-map");
  const statusElement = document.querySelector("#gamma0-map-status");
  const productTilejsonUrl =
    "https://titiler-dps-stac.maap-project.org/collections/henrydevseed__esa_biomass_gamma0_fetch_492__0.2.3__mosaic-test/WebMercatorQuad/tilejson.json";
  const layers = [
    {
      id: "hls-satellite",
      title: "HLS_S30_2.0 RGB (June 2026)",
      attribution:
        '<a href="https://lpdaac.usgs.gov/products/hlss30v2.0/" target="_blank" rel="noopener noreferrer">HLS S30 v2.0 © NASA LP DAAC</a>',
      tilejsonUrl:
        "https://openveda.cloud/api/titiler-cmr/rasterio/WebMercatorQuad/tilejson.json?collection_concept_id=C2021957295-LPCLOUD&datetime=2026-06-01T00%3A00%3A00Z%2F2026-06-30T23%3A59%3A59Z&assets=B04&assets=B03&assets=B02&exitwhenfull=true&assets_regex=B%5B0-9%5D%5B0-9A-Za-z%5D&cloud_cover=0%2C100&sort_key=cloud_cover&color_formula=Gamma RGB 3.5 Saturation 1.2 Sigmoidal RGB 15 0.35",
      visible: true,
    },
    {
      id: "beta0",
      title: "Beta0 Intensity HH/HV/VV RGB",
      attribution: "© ESA / BIOMASS Mission (L1b Derived)",
      tilejsonParams: {
        expression: "beta0_hh ** 2; beta0_hv ** 2; beta0_vv ** 2",
        rescale: [
          [0.01, 1],
          [0.000625, 0.1764],
          [0.0144, 0.64],
        ],
        asset_as_band: true,
        nodata: -9999,
        pixel_selection: "mean",
        exitwhenfull: false,
        skipcovered: false,
      },
      time_limit: 10,
      visible: false,
    },
    {
      id: "gamma0",
      title: "Linear Gamma0 HH/HV/VV RGB",
      attribution: "© ESA / BIOMASS Mission (L1b Derived)",
      tilejsonParams: {
        assets: ["gamma0_hh", "gamma0_hv", "gamma0_vv"],
        rescale: [
          [0.005, 0.33],
          [0.0003, 0.047],
          [0.007, 0.24],
        ],
        asset_as_band: true,
        nodata: -9999,
        pixel_selection: "mean",
        exitwhenfull: false,
        skipcovered: false,
      },
      time_limit: 10,
      visible: true,
    },
  ];

  if (!mapElement) {
    return;
  }

  const showError = (message) => {
    statusElement.hidden = false;
    statusElement.textContent = message;
    statusElement.classList.add("gamma0-map-status--error");
  };

  const tilejsonUrlFor = (layer) => {
    if (layer.tilejsonUrl) {
      return layer.tilejsonUrl;
    }

    const url = new URL(productTilejsonUrl);
    Object.entries(layer.tilejsonParams).forEach(([name, value]) => {
      const values = Array.isArray(value) ? value : [value];
      values.forEach((item) =>
        url.searchParams.append(
          name,
          Array.isArray(item) ? item.join(",") : item,
        ),
      );
    });
    return url.href;
  };

  const loadTilejson = async (layer) => {
    const response = await fetch(tilejsonUrlFor(layer));
    if (!response.ok) {
      throw new Error(
        `TileJSON request failed with status ${response.status}.`,
      );
    }
    return { ...layer, tilejson: await response.json() };
  };

  class LayerControl {
    constructor(mapLayers) {
      this.mapLayers = mapLayers;
    }

    onAdd(map) {
      this.map = map;
      this.container = document.createElement("details");
      this.container.className = "maplibregl-ctrl gamma0-map-layers";
      this.container.open = true;

      const summary = document.createElement("summary");
      summary.textContent = "Layers";
      this.container.append(summary);

      this.mapLayers.forEach((layer) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = layer.visible;
        input.addEventListener("change", () => {
          map.setLayoutProperty(
            layer.id,
            "visibility",
            input.checked ? "visible" : "none",
          );
        });
        label.append(input, document.createTextNode(layer.title));
        this.container.append(label);
      });

      return this.container;
    }

    onRemove() {
      this.container.remove();
      this.map = undefined;
    }
  }

  const initialiseMap = async () => {
    if (!window.maplibregl) {
      showError("The map library could not be loaded.");
      return;
    }

    try {
      const results = await Promise.allSettled(layers.map(loadTilejson));
      const mapLayers = results
        .filter((result) => result.status === "fulfilled")
        .map((result) => result.value);
      const unavailableLayers = results.flatMap((result, index) =>
        result.status === "rejected" ? [layers[index].title] : [],
      );

      if (!mapLayers.length) {
        throw new Error("No map layers could be loaded.");
      }

      const referenceLayer =
        mapLayers.find((layer) => layer.id === "gamma0") ?? mapLayers[0];
      const { tilejson } = referenceLayer;
      const map = new maplibregl.Map({
        container: mapElement,
        attributionControl: false,
        center: tilejson.center.slice(0, 2),
        zoom: 9,
        style: {
          version: 8,
          sources: Object.fromEntries(
            mapLayers.map((layer) => [
              layer.id,
              {
                type: "raster",
                tiles: layer.tilejson.tiles,
                tileSize: layer.tilejson.tileSize ?? 256,
                minzoom: layer.tilejson.minzoom,
                maxzoom: layer.tilejson.maxzoom,
                bounds: layer.tilejson.bounds,
                attribution: layer.attribution,
              },
            ]),
          ),
          layers: [
            {
              id: "background",
              type: "background",
              paint: { "background-color": "#f5f5f5" },
            },
            ...mapLayers.map((layer) => ({
              id: layer.id,
              type: "raster",
              source: layer.id,
              layout: { visibility: layer.visible ? "visible" : "none" },
            })),
          ],
        },
      });

      map.addControl(new LayerControl(mapLayers), "top-left");
      map.addControl(
        new maplibregl.AttributionControl({
          customAttribution:
            '<a href="https://maplibre.org/" target="_blank" rel="noopener noreferrer">© MapLibre contributors</a>',
        }),
        "bottom-right",
      );
      map.addControl(new maplibregl.NavigationControl(), "top-right");
      map.addControl(
        new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }),
        "top-right",
      );
      map.on("load", () => {
        if (unavailableLayers.length) {
          showError(`Unavailable layer: ${unavailableLayers.join(", ")}.`);
        } else {
          statusElement.hidden = true;
        }
      });
      map.on("error", (event) => {
        const layer = mapLayers.find((item) => item.id === event.sourceId);
        if (layer) {
          showError(
            `${layer.title} is currently unavailable. Try again later.`,
          );
        }
      });
    } catch (error) {
      showError("The map could not be loaded. Try again later.");
      console.error(error);
    }
  };

  initialiseMap();
})();
