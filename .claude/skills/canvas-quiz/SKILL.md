---
name: canvas-quiz
description: "Build a quiz from a topic, reading, lecture deck, or list of questions and create it directly in Canvas as a Classic Quiz or a New Quiz (or both). Use whenever the user wants a quiz, test, check-in, exit ticket, or practice questions put into Canvas. Trigger: `/canvas-quiz`."
trigger: /canvas-quiz
---

# /canvas-quiz

Turn source material into a Canvas quiz and create it with the Canvas MCP tools.
Both quiz engines are supported through the same question JSON:

| Engine | Create | Append | Delete | ID to keep |
|---|---|---|---|---|
| Classic Quizzes | `create_quiz` | `add_quiz_questions` | `delete_quiz` | quiz ID |
| New Quizzes | `create_new_quiz` | `add_new_quiz_items` | `delete_new_quiz` | assignment ID |

## Workflow

1. **Resolve the course.** Accept a course code or numeric ID. If the user gave neither, call `list_courses` and ask which one.
2. **Pick the engine.** Ask only if the user didn't say. Otherwise infer: run `list_new_quizzes` and `list_quizzes`; use the engine the course already has quizzes in. If both are empty, default to **New Quizzes** (the current engine) and say so. "Both" means create one quiz per engine with the same questions.
3. **Gather the material.** Read whatever the user points at before writing questions:
   - Canvas page → `get_page_content`; module → `list_modules`; file → `read_course_file`; assignment → `get_assignment_details`.
   - Google Doc → `read_google_doc`; Google Slides deck → `read_google_slides`.
   - Pasted text or a topic name → use it directly.
4. **Draft the questions** in the JSON format below. Defaults unless told otherwise: 10 questions, 1 point each, a mix of `multiple_choice` and `true_false` with one or two `short_answer`, plain-language stems, four options for multiple choice with exactly one correct, no "all of the above". Put a one-line explanation in `correct_feedback`/`incorrect_feedback` when the material supports it.
5. **Show the draft to the user before creating** unless they said to just build it. Keep the review short: title, engine, question count, points, then the questions.
6. **Create it unpublished.** Call `create_quiz` or `create_new_quiz` with `questions_json`. Pass `due_at`/`unlock_at`/`lock_at` as ISO-8601 with the course's UTC offset, `time_limit_minutes`, `allowed_attempts` (-1 = unlimited) when given. Leave `published` false unless the user explicitly says publish.
7. **Report** the ID and link the tool returns, and how many questions and points landed. If the tool replied `STOPPED`, fix the named question and pass only the remaining questions to the append tool with the returned ID; do not re-create the quiz.

## Question JSON

One array, same for both engines. Every question: `type`, `text`, optional `points` (default 1), `title`, `correct_feedback`, `incorrect_feedback`, `general_feedback`.

```json
[
  {"type": "multiple_choice", "text": "Which planet is closest to the sun?", "points": 2,
   "answers": [{"text": "Mercury", "correct": true, "feedback": "Yes."}, {"text": "Venus"}, {"text": "Mars"}]},
  {"type": "multiple_answers", "text": "Which are primes?",
   "answers": [{"text": "2", "correct": true}, {"text": "3", "correct": true}, {"text": "4"}], "partial_credit": false},
  {"type": "true_false", "text": "A square is a rectangle.", "answer": true},
  {"type": "short_answer", "text": "The capital of France is ___.", "answers": ["Paris"]},
  {"type": "essay", "text": "Argue for or against the thesis.", "points": 10, "grading_notes": "Look for a clear claim."},
  {"type": "numerical", "text": "Give pi to two decimal places.", "answer": 3.14, "margin": 0.01},
  {"type": "numerical", "text": "Pick a number in the tens.", "range": [10, 19]},
  {"type": "matching", "text": "Match the formula to its name.",
   "pairs": [{"left": "H2O", "right": "water"}, {"left": "NaCl", "right": "salt"}], "distractors": ["sugar"]},
  {"type": "file_upload", "text": "Upload your lab sheet."}
]
```

Notes:
- `multiple_choice` needs exactly one `correct: true`; `multiple_answers` needs at least one.
- `short_answer` is case-insensitive; list accepted spellings. In New Quizzes `___` in the stem marks where the blank goes (defaults to the end of the stem).
- `numerical`: `answer` + optional `margin` (absolute), or `range: [low, high]`.
- `essay` and `file_upload` are hand-graded; their points still count toward the total.
- Stems and answers may be plain text or HTML.
- The tools validate the JSON before touching Canvas and name the bad question by number.

## Guardrails

- Never publish by default. The tools add questions first, then publish only if asked, so students never see an empty quiz.
- Don't create the same quiz twice. If a create call failed after the quiz was made, the reply says so and gives the ID; append to it.
- Deleting a quiz deletes student attempts. Only call a delete tool when the user asks for that quiz by name or ID.
- Grading a quiz that students have taken is a different job: `get_new_quiz_item_analysis` (New) or `list_quiz_submissions` (Classic).
