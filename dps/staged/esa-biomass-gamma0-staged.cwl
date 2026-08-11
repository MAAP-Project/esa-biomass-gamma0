cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
$schemas:
  - >-
    https://raw.githubusercontent.com/schemaorg/schemaorg/refs/heads/main/data/releases/9.0/schemaorg-current-http.rdf
s:author:
  - class: s:Organization
    s:name: MAAP Project
s:codeRepository: https://github.com/MAAP-Project/esa-biomass-gamma0
s:softwareVersion: 0.1.3
s:version: 0.1.3
s:keywords:
  - ESA
  - BIOMASS
  - Gamma0
  - MGRS
$graph:
  - class: Workflow
    id: esa_biomass_gamma0_staged
    label: ESA BIOMASS Gamma0 MGRS staged DPS
    doc: Create fixed-grid Gamma0 products from staged local BIOMASS source files.
    inputs:
      source_item:
        label: Source STAC Item
        doc: Staged source STAC Item JSON.
        type: File
      beta0_tiff:
        label: Beta0 TIFF
        doc: Staged four-band enclosure_tiff asset.
        type: File
      radiometry_lut:
        label: Radiometry LUT
        doc: Staged enclosure_nc radiometry LUT NetCDF.
        type: File
      annotation_xml:
        label: Annotation XML
        doc: Staged enclosure_annot_xml annotation asset.
        type: File
    outputs:
      output:
        type: Directory
        outputSource: process/output
    steps:
      process:
        run: '#main'
        in:
          source_item: source_item
          beta0_tiff: beta0_tiff
          radiometry_lut: radiometry_lut
          annotation_xml: annotation_xml
        out:
          - output
  - class: CommandLineTool
    id: main
    requirements:
      DockerRequirement:
        # x-release-please-start-version
        dockerPull: ghcr.io/maap-project/esa-biomass-gamma0-staged:v0.1.3
        # x-release-please-end
      NetworkAccess:
        networkAccess: true
      ResourceRequirement:
        ramMin: 16
        coresMin: 8
        outdirMax: 20
    baseCommand: /app/esa-biomass-gamma0/dps/staged/run.sh
    successCodes:
      - 0
    inputs:
      source_item:
        type: File
        inputBinding:
          position: 1
          prefix: '--source-item'
      beta0_tiff:
        type: File
        inputBinding:
          position: 2
          prefix: '--beta0-tiff'
      radiometry_lut:
        type: File
        inputBinding:
          position: 3
          prefix: '--radiometry-lut'
      annotation_xml:
        type: File
        inputBinding:
          position: 4
          prefix: '--annotation-xml'
    outputs:
      output:
        type: Directory
        outputBinding:
          glob: output
