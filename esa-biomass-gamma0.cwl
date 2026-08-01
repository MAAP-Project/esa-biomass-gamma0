cwlVersion: v1.2

$graph:
  - class: Workflow
    id: esa-biomass-gamma0
    label: ESA BIOMASS Gamma0 MGRS DPS
    doc: >-
      Create fixed-grid 25 m MGRS Beta0 and linear Gamma0 products from one
      staged ESA BIOMASS Level-1B source Item and its local inputs.
    inputs:
      source_item:
        type: File
      beta0_tiff:
        type: File
      radiometry_lut:
        type: File
      annotation_xml:
        type: File
      resolution:
        type: double
        default: 25
      overwrite:
        type: boolean
        default: false
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
          resolution: resolution
          overwrite: overwrite
        out: [output]

  - class: CommandLineTool
    id: main
    requirements:
      DockerRequirement:
        dockerPull: esa-biomass-gamma0:latest
      NetworkAccess:
        networkAccess: false
      ResourceRequirement:
        ramMin: 8
        coresMin: 4
        outdirMax: 20
    baseCommand: /app/esa-biomass-gamma0/run.sh
    successCodes: [0]
    inputs:
      source_item:
        type: File
        inputBinding:
          position: 1
          prefix: --source-item
      beta0_tiff:
        type: File
        inputBinding:
          position: 2
          prefix: --beta0-tiff
      radiometry_lut:
        type: File
        inputBinding:
          position: 3
          prefix: --radiometry-lut
      annotation_xml:
        type: File
        inputBinding:
          position: 4
          prefix: --annotation-xml
      resolution:
        type: double
        default: 25
        inputBinding:
          position: 5
          prefix: --resolution
      overwrite:
        type: boolean
        default: false
        inputBinding:
          position: 6
          prefix: --overwrite
    outputs:
      output:
        type: Directory
        outputBinding:
          glob: output
