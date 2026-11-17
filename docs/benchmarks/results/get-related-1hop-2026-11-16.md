# FetchCore get_related one-hop evaluation

- Status: **failed**
- Repository: `oalangomes/fetchcore`
- Revision: `fa94f4ee456710f348d05f16425c5b5a016c867c`
- Collection: `fc-related-eval-20261116`
- Scenarios: 7/8 passed
- Expected/matched/missing/unexpected: 4/4/0/0
- Exact relationship match rate: 1.0000
- Provenance validity: 1.0000
- MCP calls (related/source): 8/1
- Tool/contract errors: 0/0
- Latency p50/p95 (ms): 3744.0/7504.0

| Scenario | Outcome | Coverage | Matched | Missing | Unexpected | Source reads |
| --- | --- | --- | ---: | ---: | ---: | --- |
| resolved-call-exact-target | passed | success | 1 | 0 | 0 | 1/1 |
| resolved-call-incoming | failed | partial | 1 | 0 | 0 | 0/0 |
| homonym-unconfirmed-call | passed | success | 1 | 0 | 0 | 0/0 |
| unconfirmed-is-not-resolved | passed | not_found | 0 | 0 | 0 | 0/0 |
| file-import-limit-and-partial-coverage | passed | partial | 1 | 0 | 0 | 0/0 |
| unsupported-relation-family | passed | unsupported | 0 | 0 | 0 | 0/0 |
| unknown-source | expected_tool_error | - | 0 | 0 | 0 | 0/0 |
| unsupported-incoming-file | expected_tool_error | - | 0 | 0 | 0 | 0/0 |
