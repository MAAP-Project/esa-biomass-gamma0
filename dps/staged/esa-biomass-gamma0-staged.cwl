cwlVersion: v1.2

$graph:
  - class: Workflow
    id: esa-biomass-gamma0-staged
    label: ESA BIOMASS Gamma0 MGRS staged DPS
    doc: Create fixed-grid Gamma0 products from staged local BIOMASS source files.
    inputs:
      source_item:
        type: File
      beta0_tiff:
        type: File
      radiometry_lut:
        type: File
      annotation_xml:
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
    baseCommand: /app/esa-biomass-gamma0/dps/staged/run.sh
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
    outputs:
      output:
        type: Directory
        outputBinding:
          glob: output
