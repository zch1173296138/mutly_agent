## ADDED Requirements

### Requirement: Formal cases include human review metadata
The dataset SHALL distinguish draft evaluation cases from cases approved for formal evaluation by storing human review metadata on every formal case.

#### Scenario: Approved case is eligible for formal reporting
- **WHEN** a dataset case is used for a formal A/B conclusion
- **THEN** the case contains `label_review.status` equal to `approved`, a non-empty reviewer identifier, a review date, a confidence value other than `low`, and non-empty review notes

#### Scenario: Draft case is excluded from formal reporting
- **WHEN** a dataset case has missing review metadata or `label_review.status` other than `approved`
- **THEN** the case is treated as development-only and is not counted as formal evidence that one agent architecture solved another architecture's loop behavior

### Requirement: Gold answers are claim-backed
Every formal evaluation case MUST map each material `gold_answer` claim to supporting evidence, a recomputable calculation, or an explicitly reviewed inference.

#### Scenario: Direct evidence supports a claim
- **WHEN** a formal case declares a direct evidence-backed gold answer claim
- **THEN** the claim references an evidence item whose quote is present in the declared local source file

#### Scenario: Calculation supports a claim
- **WHEN** a formal case declares a calculation-backed gold answer claim
- **THEN** the case includes calculation metadata that can be recomputed by tests to match the stored result

#### Scenario: Human inference supports a claim
- **WHEN** a formal case declares an inference-backed gold answer claim that cannot be fully validated by script
- **THEN** the claim records that it was reviewed by a human and the case includes approved review metadata

### Requirement: Golden-set filtering is explicit
The evaluation workflow SHALL make the reviewed golden subset explicit when generating results intended as formal evidence.

#### Scenario: Formal report filters to reviewed cases
- **WHEN** an A/B report is generated for a formal conclusion
- **THEN** the report uses only approved, non-low-confidence cases or marks itself as non-evidentiary

#### Scenario: Development run can include drafts
- **WHEN** a developer intentionally runs draft or unreviewed cases
- **THEN** the run is allowed but the output does not claim formal dataset evidence

### Requirement: Adapted open-source samples pass local review
Open-source benchmark samples SHALL NOT enter the golden set until they are converted into the local schema and pass the same source, gold answer, loop rule, and human review requirements as native samples.

#### Scenario: External sample is converted
- **WHEN** a sample from an external benchmark is added to the evaluation dataset
- **THEN** it declares local available sources, local evidence quotes or calculations, local loop rules, and label review metadata before it is eligible for formal reporting
