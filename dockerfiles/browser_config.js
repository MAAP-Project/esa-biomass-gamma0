export default {
  catalogUrl: "http://localhost:8081",
  catalogTitle: "ESA BIOMASS Gamma0",
  allowExternalAccess: true,
  displayGeoTiffByDefault: false,
  displayPreview: true,
  displayOverview: true,
  buildTileUrlTemplate: (asset) => {
    const href = asset.getAbsoluteUrl();
    const assetHref = asset.href || href;
    const tileHref = assetHref.startsWith("/vsi") ? assetHref : href;

    return (
      "http://localhost:8082/external/tiles/WebMercatorQuad/{z}/{x}/{y}?url=" +
      encodeURIComponent(tileHref)
    );
  },
  pathPrefix: "/",
  historyMode: "history",
  showThumbnailsAsAssets: false,
};
