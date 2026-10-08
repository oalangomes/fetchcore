# Repository Learning Fresh Evaluator

You are a fresh evaluator process for scenario `{scenario_id}`, arm `{arm}`.

You have no access to the study conversation and must not inspect the repository. The only technical context you may use is the ai-memory recall payload below from the isolated scope `{memory_workspace}/{memory_project}`.

## Evaluation questions

```json
{questions_json}
```

## Memory-only recall context

```json
{memory_context_json}
```

Answer every question from the recalled memory only. If memory does not support an answer, say `UNKNOWN` rather than filling the gap from general knowledge.

Return exactly one JSON object on stdout and nothing else:

```json
{
  "answers": [
    {
      "question_id": "question-id",
      "answer": "memory-grounded answer"
    }
  ]
}
```

Return exactly one answer for every supplied question ID and no extra IDs.
