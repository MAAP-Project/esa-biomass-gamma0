cwlVersion: v1.2

$namespaces:
  s: https://schema.org/
$schemas:
  - https://raw.githubusercontent.com/schemaorg/schemaorg/refs/heads/main/data/releases/9.0/schemaorg-current-http.rdf

s:author:
  - class: s:Organization
    s:name: MAAP Project
s:codeRepository: https://github.com/MAAP-Project/esa-biomass-gamma0
s:softwareVersion: 0.1.0 # x-release-please-version
s:version: 0.1.0 # x-release-please-version
s:keywords: [ESA, BIOMASS, Gamma0, MGRS]

$graph:
  - class: Workflow
    id: esa_biomass_gamma0_fetch
    label: ESA BIOMASS Gamma0 MGRS fetch DPS
    doc: Fetch one BIOMASS source Item by ID and create fixed-grid Gamma0 products.
    inputs:
      item_id:
        label: Source STAC Item ID
        doc: BIOMASS Level-1B STAC Item ID to fetch inside the job.
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
        dockerPull: ghcr.io/maap-project/esa-biomass-gamma0-fetch:v0.1.0 # x-release-please-version
      NetworkAccess:
        networkAccess: true
      ResourceRequirement:
        ramMin: 16
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
