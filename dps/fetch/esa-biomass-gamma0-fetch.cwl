cwlVersion: v1.2

$graph:
  - class: Workflow
    id: esa-biomass-gamma0-fetch
    label: ESA BIOMASS Gamma0 MGRS fetch DPS
    doc: Fetch one BIOMASS source Item by ID and create fixed-grid Gamma0 products.
    inputs:
      item_id:
        type: string
    outputs:
      output:
        type: Directory
        outputSource: process/output
    steps:
      process:
        run: '#main'
        in:
          item_id: item_id
        out: [output]

  - class: CommandLineTool
    id: main
    requirements:
      DockerRequirement:
        dockerPull: esa-biomass-gamma0:latest
      NetworkAccess:
        networkAccess: true
      ResourceRequirement:
        ramMin: 8
        coresMin: 4
        outdirMax: 20
    baseCommand: /app/esa-biomass-gamma0/dps/fetch/run.sh
    successCodes: [0]
    inputs:
      item_id:
        type: string
        inputBinding:
          position: 1
    outputs:
      output:
        type: Directory
        outputBinding:
          glob: output
