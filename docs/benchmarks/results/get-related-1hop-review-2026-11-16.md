# FetchCore get_related one-hop evaluation

- Status: **passed**
- Repository: `oalangomes/fetchcore`
- Revision: `fa94f4ee456710f348d05f16425c5b5a016c867c`
- Collection: `fc-related-pr251-review`
- Scenarios: 8/8 passed
- Expected/matched/missing/unexpected: 4/4/0/0
- Exact relationship match rate: 1.0000
- Response provenance validity: 1.0000 (6/6; includes empty responses)
- Relationship provenance validity: 1.0000 (4/4)
- MCP calls (related/source): 8/1
- Tool/contract errors: 0/0
- Latency p50/p95 (ms): 3765.0/7476.0

| Scenario | Outcome | Coverage | Expected contract status | Coverage goal | Matched | Missing | Unexpected | Source reads |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| resolved-call-exact-target | passed | success | success | - | 1 | 0 | 0 | 1/1 |
| resolved-call-incoming | passed | partial | partial | success | 1 | 0 | 0 | 0/0 |
| homonym-unconfirmed-call | passed | success | success | - | 1 | 0 | 0 | 0/0 |
| unconfirmed-is-not-resolved | passed | not_found | not_found | - | 0 | 0 | 0 | 0/0 |
| file-import-limit-and-partial-coverage | passed | partial | partial | - | 1 | 0 | 0 | 0/0 |
| unsupported-relation-family | passed | unsupported | unsupported | - | 0 | 0 | 0 | 0/0 |
| unknown-source | expected_tool_error | - | - | - | 0 | 0 | 0 | 0/0 |
| unsupported-incoming-file | expected_tool_error | - | - | - | 0 | 0 | 0 | 0/0 |
