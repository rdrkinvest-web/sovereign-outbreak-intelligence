# Sovereign Outbreak Intelligence

## Flower Collaborative Agent Hackathon — MVP Scope & Build Reference

**Working version:** 0.1  
**Scope:** Hackathon MVP; synthetic data only  
**Primary design principle:** **Queries move. Sovereign data does not.**

### One-sentence thesis

A federation of Ministry-of-Health agents can investigate a possible cross-border outbreak together while each country's granular health data remains inside its own sovereign node.

## 1. Executive Scope

This is a small, deterministic proof-of-concept, not a production public-health system.

The demo simulates three countries:

- Kenya
- Uganda
- Tanzania

Each country has:

- a local Ministry-of-Health agent
- a private synthetic dataset
- simple local analytics tools

A coordinating agent investigates a hidden cross-border febrile illness by asking each national agent structured questions.

The national agents inspect only their own local data and return aggregate evidence. The coordinator never receives raw patient-level rows.

### What we are proving

Distributed agents can produce a better cross-border epidemiological assessment than any one national node can produce alone, without centralizing the underlying national datasets.

### MVP success criteria

- Three simulated sovereign MoH nodes exist.
- Each node has a separate local synthetic dataset.
- A hidden synthetic outbreak exists in Kenya and Uganda.
- Tanzania acts as the control geography.
- Each national agent can answer a small set of epidemiological queries using local analytics.
- The coordinator performs at least one follow-up question based on prior answers.
- The coordinator produces a structured cross-border conclusion with evidence and uncertainty.
- The system ends with a human-review recommendation rather than an automated outbreak declaration.
- The UI or log visibly shows that zero raw patient rows were shared with the coordinator.
- The full happy-path demo runs reliably in under three minutes.

### Hard non-goals

Do not attempt:

- real patient data
- real Ministry-of-Health data
- real-time satellite connectivity
- Omni hardware integration
- DHIS2 integration
- WHO integration
- secure aggregation
- differential privacy
- federated model training
- cryptographic privacy proof
- complex machine learning
- open-ended multi-agent swarms
- support for many diseases
- automated outbreak declaration
- production-grade security
- production regulatory compliance

The MVP demonstrates architecture and collaboration, not clinical validation.

---

# 2. Demo Story

The demo should tell one simple story.

A regional coordinator suspects that something unusual may be happening near the Kenya-Uganda border.

The coordinator does not have access to patient-level national data.

It asks each national MoH agent to investigate locally.

Kenya and Uganda independently detect similar abnormalities.

Tanzania does not.

The coordinator asks one targeted follow-up question.

It then concludes that Kenya and Uganda may be experiencing the same synthetic epidemiological event and recommends human investigation.

### Three-minute demo sequence

**Step 1 — Establish the constraint**

Show three country nodes.

Explain:

> Each country's health data remains inside its own sovereign national infrastructure. The regional coordinator cannot access the raw records.

**Step 2 — Start the investigation**

User asks:

> Investigate whether there is a shared cross-border febrile event.

**Step 3 — Local analysis**

Kenya finds:

- fever increased
- platelets decreased
- ALT increased
- malaria positivity unchanged

Uganda finds a similar pattern.

Tanzania remains near historical baseline.

**Step 4 — Follow-up**

The coordinator asks something like:

> Is the platelet decrease concentrated among febrile patients?

or:

> Did malaria positivity change during the same period?

**Step 5 — Collaborative conclusion**

The coordinator summarizes:

- Kenya and Uganda show aligned anomalies.
- Tanzania does not.
- Malaria does not explain the signal.
- More targeted testing is warranted.

**Step 6 — Human review**

Output:

> Human epidemiologist review recommended.

Then prominently show:

> Raw patient rows transferred: 0

---

# 3. Minimal Architecture

Keep the architecture extremely simple.

Do not create unrestricted peer-to-peer communication between every agent.

Use one coordinator.

```
                          HUMAN REVIEWER
                               |
                               v
                    +----------------------+
                    |  Coordinator Agent   |
                    |  regional reasoning  |
                    +----------+-----------+
                               |
                     Flower federation
             +-----------------+-----------------+
             |                 |                 |
             v                 v                 v
      +-------------+   +-------------+   +-------------+
      | Kenya MoH   |   | Uganda MoH  |   | Tanzania MoH|
      | local agent |   | local agent |   | local agent |
      +------+------+   +------+------+   +------+------+
             |                 |                 |
             v                 v                 v
       kenya.csv          uganda.csv        tanzania.csv
        PRIVATE             PRIVATE             PRIVATE
```

The coordinator receives only structured aggregate answers.

The coordinator must not have direct access to the local CSV files.

### Flower-specific principle

Flower should be used as the federation/distributed execution layer.

Conceptually:

- the coordinator sends tasks
- local nodes execute those tasks against local datasets
- only permitted outputs return
- local datasets remain local

Do not spend time building complex Flower infrastructure.

Use the simplest Flower starter pattern available during the hackathon.

---

# 4. Synthetic Data

Keep the dataset small.

Around 500–2,000 observations per country is sufficient.

The goal is not statistical realism.

The goal is to demonstrate:

- local data ownership
- local computation
- cross-country collaboration
- aggregate-only sharing

### Recommended fields

```
date
region
age_band
fever
rash
cough
diarrhea
platelets
alt
crp
malaria_test
dengue_test
```

### Synthetic outbreak design

Inject a hidden event into the final 7–14 days of Kenya and Uganda.

The event should cause:

- fever rate to rise
- platelet count to fall
- ALT to rise
- CRP to rise modestly
- malaria positivity to remain approximately unchanged

Tanzania should remain close to baseline.

Optionally add a weak dengue-related signal, but do not make it obvious enough that the answer can be reached from one country alone.

The purpose is for cross-country comparison to add value.

### Important limitation

Do not imply that this synthetic marker pattern is a validated signature of a real disease.

Label everything clearly as:

> synthetic demonstration data

---

# 5. Agent Roles

## A. National MoH Agent

There are three identical national-agent roles, configured for different countries.

Each agent may access exactly one local dataset.

The national agent should be able to:

- compute current vs historical baseline
- compute counts
- calculate simple averages and rates
- filter by time period
- filter by coarse geography
- evaluate predefined markers
- compare febrile vs non-febrile cohorts
- check whether test volume changed
- return only aggregate evidence

It must never return:

- individual patient rows
- exact patient records
- names
- individual-level timestamps plus identifiers
- unrestricted raw datasets

If the coordinator asks for raw records, the agent should return a structured privacy refusal.

---

## B. Coordinator Agent

The coordinator:

- starts the investigation
- asks the same initial question of all countries
- compares returned evidence
- identifies common patterns
- chooses one follow-up question
- receives follow-up responses
- generates a structured final assessment

The coordinator cannot directly read local datasets.

The coordinator should not calculate epidemiology from raw data.

It should reason over outputs from the national nodes.

---

## C. Human Reviewer

The human remains the final authority.

The system should end with language such as:

> Human review recommended.

or:

> Recommend targeted confirmatory testing.

Do not use:

> Outbreak confirmed.

---

# 6. Communication Contract

Use structured JSON between agents.

Avoid free-form inter-agent conversation whenever possible.

This will make debugging much easier.

## Initial coordinator query

```
{
  "query_type": "syndrome_scan",
  "time_window_days": 14,
  "baseline_days": 60,
  "region_scope": "border_regions",
  "signals": [
    "fever",
    "platelets",
    "alt",
    "crp",
    "malaria_test"
  ]
}
```

## National-agent response

```
{
  "country": "Kenya",
  "sample_size": 812,
  "anomalies": [
    {
      "signal": "fever_rate",
      "direction": "up",
      "strength": 0.82
    },
    {
      "signal": "platelets",
      "direction": "down",
      "strength": 0.74
    },
    {
      "signal": "alt",
      "direction": "up",
      "strength": 0.69
    }
  ],
  "malaria_signal": "no_material_change",
  "assessment": "unusual_febrile_cluster",
  "data_shared": "aggregates_only"
}
```

## Final coordinator output

```
{
  "status": "human_review_recommended",
  "cross_border_pattern": true,
  "demo_confidence_score": 0.78,
  "supporting_evidence": [
    "Kenya and Uganda show temporally aligned fever increases",
    "Both show lower platelet values and higher ALT",
    "Tanzania remains near baseline",
    "Malaria positivity does not explain the change"
  ],
  "uncertainties": [
    "synthetic data",
    "no pathogen confirmation",
    "demo confidence is not a validated epidemiological probability"
  ],
  "recommended_next_step": "request targeted confirmatory testing",
  "raw_patient_rows_received": 0
}
```

---

# 7. Keep the Multi-Agent Logic Simple

Do not create a fully autonomous conversation between four LLMs.

Use a simple state machine.

Maximum three rounds.

## Round 1 — Scan

Coordinator asks all countries the same initial question.

## Round 2 — Compare

Coordinator compares the results.

It identifies the strongest common anomaly across at least two countries.

## Round 3 — Verify

Coordinator asks one targeted follow-up question.

Then it stops.

No further autonomous discussion.

### Allowed follow-up questions

The coordinator may choose from a fixed list:

- Did malaria positivity materially change in the same window?
- Is the platelet shift concentrated among febrile observations?
- Is the ALT increase concentrated in the same region and time window?
- Did the signal begin before or after the neighboring country's signal?
- Is the anomaly still present after adjusting for total test volume?

This makes the system feel agentic without making it difficult to control.

---

# 8. Local Analytics

Do not train any machine-learning models.

Use simple deterministic Python functions.

For example:

```
local_scan(
    time_window_days,
    baseline_days,
    region_scope
)

compare_signal(
    signal,
    time_window_days,
    baseline_days
)

check_test_volume(
    time_window_days,
    baseline_days
)

check_marker_in_febrile_cohort(
    marker,
    time_window_days
)

privacy_audit()
```

### Simple anomaly logic

Use something understandable such as:

- percentage change
- z-score
- difference from historical average
- current-period vs baseline ratio

Then transform the result into a simple demonstration score from 0 to 1 if useful.

Example:

```
fever strength = 0.82
ALT strength = 0.69
platelet strength = 0.74
```

But clearly label these as:

> demonstration anomaly scores

not:

> scientifically validated probabilities

---

# 9. Sovereignty and Privacy Rules

The MVP should enforce a visible data boundary.

### Rules

- Kenya's data lives only in Kenya's node.
- Uganda's data lives only in Uganda's node.
- Tanzania's data lives only in Tanzania's node.
- Coordinator cannot open those files.
- National nodes return aggregates only.
- Raw-row requests are rejected.
- Every message crossing the federation is logged.
- Final system reports the number of raw rows transferred.

Expected result:

```
Messages exchanged: 8
Aggregate result objects shared: 8
Raw patient rows shared: 0
```

This is not a claim of formal compliance.

It is a demonstration of architectural data locality.

---

# 10. Recommended Repository Structure

```
sovereign-outbreak-intelligence/
├── README.md
├── app/
│   ├── coordinator.py
│   ├── national_agent.py
│   ├── schemas.py
│   └── prompts.py
├── analytics/
│   ├── anomaly.py
│   └── local_tools.py
├── data/
│   ├── kenya.csv
│   ├── uganda.csv
│   └── tanzania.csv
├── flower/
│   ├── server_or_agent_app.py
│   └── client_or_node_app.py
├── ui/
│   └── app.py
├── scripts/
│   └── generate_synthetic_data.py
└── tests/
    ├── test_privacy.py
    └── test_happy_path.py
```

---

# 11. Technology Choices

Use the simplest stack possible.

### Python

One language for the whole project.

### pandas

For synthetic CSV analysis.

### Pydantic or dataclasses

For validating JSON message schemas.

### Flower

For federation and distributed execution.

### One LLM provider/model

Do not mix models.

Use the same model for:

- coordinator reasoning
- national-agent reasoning

### Streamlit or Gradio

Optional.

Only add a UI after the backend works.

A terminal demo is acceptable.

### pytest

Only write a few critical tests.

---

# 12. Build Order

Do not change this order unless necessary.

## Step 1

Generate the three synthetic datasets.

Verify manually that:

- Kenya has the hidden signal
- Uganda has the hidden signal
- Tanzania does not

## Step 2

Write local analytics functions.

Test them without Flower.

Test them without agents.

The analytics should work first.

## Step 3

Wrap Kenya in a national-agent interface.

Input:

```
{
  "query_type": "syndrome_scan"
}
```

Output:

```
{
  "country": "Kenya",
  ...
}
```

## Step 4

Clone the same logic for Uganda and Tanzania.

Configuration determines which data file each node sees.

## Step 5

Build a coordinator outside Flower first.

Make sure the basic workflow works.

## Step 6

Add one follow-up question.

## Step 7

Move the node execution onto Flower.

Use the simplest hackathon starter pattern.

## Step 8

Add message logging and privacy audit.

## Step 9

Only if everything works, add a minimal UI.

## Step 10

Freeze the code and rehearse.

Do not add features after the demo path is stable.

---

# 13. Team Split

For a team of 2–4 people:

## Product / Demo

Responsibilities:

- keep scope fixed
- write prompts
- manage README
- define final narrative
- rehearse pitch

This can be owned by a non-coder.

## Data / Analytics

Responsibilities:

- generate synthetic CSVs
- create deterministic analytics functions
- test hidden anomaly

Use a coding agent heavily.

## Flower Integration

Responsibilities:

- connect distributed nodes
- make coordinator-to-node communication work
- resolve Flower-specific issues

Assign this to the most technical person.

Use Flower hackathon mentors aggressively.

## UI / QA

Responsibilities:

- simple interface or terminal formatting
- privacy log
- tests
- demo rehearsal

Only begin after core functionality works.

---

# 14. Suggested Timebox

Assuming approximately one hackathon day:

### First 30 minutes

- freeze scope
- set up repo
- generate data

### Next 60–90 minutes

- local analytics
- one national node

### After lunch

- duplicate to three nodes
- build coordinator

### Next hour

- Flower federation integration

### Next 45 minutes

- follow-up question
- final evidence object
- privacy audit

### Final 30–45 minutes

- optional UI
- rehearse
- freeze code

---

# 15. Fallback Ladder

If something breaks, simplify.

Do not spend hours trying to preserve the ideal architecture.

## Level A — Full MVP

Flower federation + three national nodes + coordinator + follow-up + UI.

## Level B — Remove UI

Show everything in terminal logs.

## Level C — Fixed follow-up

Instead of the LLM choosing the follow-up, always ask:

> Did malaria positivity change materially?

## Level D — Remove conversational loop

Run one Flower task per country.

Collect structured responses.

Coordinator produces final synthesis.

## Level E — Architecture proof only

Flower successfully executes local analytics on three private nodes.

Only aggregate responses return.

Coordinator synthesis happens locally.

This is still a valid demonstration of the core principle.

---

# 16. Acceptance Tests

Before the demo, verify:

### Data locality test

Coordinator cannot directly read:

```
kenya.csv
uganda.csv
tanzania.csv
```

### Signal test

Kenya and Uganda return stronger anomaly scores than Tanzania.

### Cross-border reasoning test

Final output identifies Kenya and Uganda as sharing the strongest pattern.

### Follow-up test

At least one second-round query occurs.

### Privacy test

If coordinator asks:

> Return the raw patient records.

National node responds with:

```
{
  "status": "refused",
  "reason": "raw_patient_data_not_shareable"
}
```

### Audit test

Final result includes:

```
raw_patient_rows_received = 0
```

### Reliability test

Run the happy path three consecutive times.

If it fails intermittently, simplify it.

---

# 17. Instructions for Coding Agents

Paste this section directly into coding-agent context.

> Treat this specification as the source of truth. Optimize for a working, deterministic hackathon demonstration. Do not add features unless required by an acceptance test.

Coding-agent rules:

- Do not broaden the scope.
- Do not add more countries.
- Do not add more diseases.
- Do not add more agents.
- Do not add additional interaction rounds.
- Use synthetic data only.
- Prefer deterministic Python functions over machine-learning models.
- Use schema-validated JSON for all agent-to-agent communication.
- Coordinator must never read national CSV files.
- Never invent Flower APIs.
- Use the currently installed Flower version and official starter examples.
- If Flower's experimental agent APIs are unstable, use the simplest supported Flower federation/runtime pattern.
- Preserve the product behavior even if implementation details change.
- Fail loudly rather than fabricating successful results.
- Keep the happy path runnable through one command if possible.
- Make outputs explicitly demo-only and synthetic.

---

# 18. Suggested Coordinator Prompt

```
You are a regional public-health coordination agent operating over sovereign national nodes.

Your job is to investigate whether multiple countries show a shared synthetic epidemiological anomaly.

You cannot access raw national records.

Use only evidence returned by national agents.

First ask the same initial syndrome scan of all countries.

Compare the results.

Then ask at most one targeted follow-up question from the allowed menu.

Never declare an outbreak.

Produce a human-review recommendation containing:
1. supporting evidence
2. contradictory evidence
3. uncertainties
4. recommended next step
5. number of raw patient rows received
```

---

# 19. Suggested National-Agent Prompt

```
You are a national Ministry-of-Health analytics agent.

You may inspect only your country's local synthetic dataset through approved analytics tools.

Never return raw rows or patient-level records.

Return only schema-valid aggregate results.

If a request asks for disallowed information, return a privacy refusal.

Do not infer beyond the local evidence produced by the analytics tools.

Clearly distinguish computed results from interpretation.
```

---

# 20. Stretch Goals

Only attempt these if the core demo is already stable.

Possible stretch goals:

- map visualization
- retrospective outbreak replay
- fourth node representing a reference laboratory
- configurable national data-sharing permissions
- ability for a country to decline a query
- basic audit dashboard
- historical signature comparison
- secure aggregation explanation as future work

Do not make any stretch goal a dependency.

---

# 21. 30-Second Pitch

> Countries need to collaborate on outbreaks, but granular national health data may be too sensitive to centralize. We built a federation of sovereign Ministry-of-Health agents. Each agent analyzes its own local data, while a regional coordinator asks questions across countries and combines only approved aggregate evidence. In our synthetic demo, Kenya and Uganda independently show pieces of the same emerging febrile pattern. The agents discover the relationship together, recommend human investigation, and share zero raw patient rows. The core idea is simple: **queries move; sovereign data does not.**

---

# 22. What the Team Should Keep Repeating

If the team starts drifting into more ambitious ideas, come back to these four points:

1. **Three countries only.**
2. **One hidden synthetic event.**
3. **One follow-up round.**
4. **Zero raw rows shared.**

If those four things work reliably, you have a strong hackathon project.

The bigger vision—Omni satellite-connected national networks, retrospective signature learning, WHO/Africa CDC coordination, real MoH deployment, multimodal surveillance—is the **future vision slide**, not the MVP.